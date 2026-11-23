"""Backup sets of the backend databases: storage/backups/<UTC time>-<label>/ (ITGLASSES_BACKUP_DIR).

Each set holds a consistent, verified copy (SQLite backup API + integrity_check) of the device database (users,
sessions, audit, devices) and the chat history, plus manifest.json (file names, sizes, schema versions; never any
content). Optionally the knowledge database (index and metadata only: the original documents are files in
storage/knowledge and are not copied).

  manual: python -m backend.app.backups [--include-knowledge]   (scripts\\backup-data.ps1)
  daily:  the backend makes a "daily" set at startup and then every hour checks whether the last one is older
          than 24 h; it keeps the last ITGLASSES_DAILY_BACKUP_KEEP (7). ITGLASSES_DAILY_BACKUP=off disables it.

A file of a set is restored with scripts\\restore-pairing-backup.ps1 (devices) or -History (history).
"""
import argparse
import asyncio
import json
import logging
import os
import secrets
import shutil
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

from backend.app.history import store as history_store
from backend.app.pairing import store as device_store

logger = logging.getLogger(__name__)

DEFAULT_DIR = device_store.PROJECT_ROOT / "storage" / "backups"
KNOWLEDGE_DB = device_store.PROJECT_ROOT / "storage" / "knowledge" / "knowledge.sqlite3"
DAILY_LABEL = "daily"
DAILY_SECONDS = 24 * 3600
CHECK_SECONDS = 3600
KEEP_VARIABLE, KEEP_DEFAULT, KEEP_RANGE = "ITGLASSES_DAILY_BACKUP_KEEP", 7, (1, 60)
ENABLED_VARIABLE = "ITGLASSES_DAILY_BACKUP"
ON, OFF = {"1", "true", "yes", "on"}, {"0", "false", "no", "off"}


class BackupConfigError(ValueError):
    code = "backup_config"


def backup_root():
    configured = os.environ.get("ITGLASSES_BACKUP_DIR", "").strip()
    return Path(configured) if configured else DEFAULT_DIR


def daily_enabled():
    raw = (os.environ.get(ENABLED_VARIABLE) or "").strip().lower()
    if not raw or raw in ON:
        return True
    if raw in OFF:
        return False
    raise BackupConfigError(f"{ENABLED_VARIABLE} debe ser on u off (valor actual: {raw[:20]!r}).")


def daily_keep():
    raw = (os.environ.get(KEEP_VARIABLE) or "").strip()
    if not raw:
        return KEEP_DEFAULT
    low, high = KEEP_RANGE
    if not (raw.isascii() and raw.isdecimal()) or not low <= int(raw) <= high:
        raise BackupConfigError(f"{KEEP_VARIABLE} debe ser un numero entero entre {low} y {high} "
                                f"(valor actual: {raw[:20]!r}).")
    return int(raw)


def check_settings():
    """(enabled, keep); BackupConfigError for an invalid value, so the backend refuses to start."""
    return daily_enabled(), daily_keep()


def _sources(include_knowledge):
    """[(name in the set, live database path)] of the databases that exist."""
    sources = [("devices.sqlite3", device_store.database_path()), ("history.sqlite3", history_store.database_path())]
    if include_knowledge:
        sources.append(("knowledge.sqlite3", KNOWLEDGE_DB))
    return [(name, Path(path)) for name, path in sources if Path(path).is_file() and Path(path).stat().st_size > 0]


def _copy(source, target):
    """Consistent copy of a live database, verified; (bytes, user_version)."""
    with closing(sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as live, \
            closing(sqlite3.connect(target)) as copy:
        live.backup(copy)
        healthy = copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        version = copy.execute("PRAGMA user_version").fetchone()[0]
    if not healthy:
        raise device_store.BackupError(f"backup of {Path(source).name} could not be verified")
    return Path(target).stat().st_size, version


def backup_all(label="manual", include_knowledge=False, root=None, now=None):
    """A new backup set; returns its folder. A set that cannot be completed is removed (never half a set)."""
    if not label.isascii() or not label.replace("-", "").isalnum():
        raise ValueError("backup label must be letters, digits or '-'")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(time.time() if now is None else now))
    folder = Path(root or backup_root()) / f"{stamp}-{label}-{secrets.token_hex(2)}"
    folder.mkdir(parents=True)
    manifest = {"created_at": int(time.time() if now is None else now), "label": label, "files": []}
    try:
        for name, source in _sources(include_knowledge):
            size, version = _copy(source, folder / name)
            manifest["files"].append({"name": name, "bytes": size, "schema_version": version})
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return folder


def _sets(root, label):
    if not Path(root).is_dir():
        return []
    return sorted((p for p in Path(root).iterdir() if p.is_dir() and f"-{label}-" in p.name
                   and (p / "manifest.json").is_file()), key=lambda p: p.name)


def latest(label=DAILY_LABEL, root=None):
    found = _sets(root or backup_root(), label)
    return found[-1] if found else None


def daily_backup(root=None, now=None, keep=None):
    """A daily set when the last one is older than 24 h (or missing); prunes beyond ``keep``. The new folder, or
    None when nothing was due."""
    root = Path(root or backup_root())
    now = time.time() if now is None else now
    keep = daily_keep() if keep is None else keep
    last = latest(DAILY_LABEL, root)
    if last is not None:
        try:
            made = json.loads((last / "manifest.json").read_text(encoding="utf-8"))["created_at"]
        except (OSError, ValueError, KeyError):
            made = 0
        if now - made < DAILY_SECONDS:
            return None
    folder = backup_all(DAILY_LABEL, root=root, now=now)
    for old in _sets(root, DAILY_LABEL)[:-keep]:
        shutil.rmtree(old, ignore_errors=True)
    logger.info("Daily backup made folder=%s files=%d", folder.name,
                len(json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["files"]))
    return folder


async def run_daily_backups(interval=CHECK_SECONDS):
    """Background task of the backend: never raises, never blocks the startup; cancelled at shutdown."""
    while True:
        try:
            await asyncio.to_thread(daily_backup)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a failed backup is logged and retried at the next check
            logger.exception("Daily backup failed")
        await asyncio.sleep(interval)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m backend.app.backups",
                                     description="Respaldo de las bases de ITGlasses (dispositivos e historial).")
    parser.add_argument("--include-knowledge", action="store_true",
                        help="incluye la base de manuales (solo el indice; no los archivos originales)")
    parser.add_argument("--list", action="store_true", help="lista los respaldos existentes")
    args = parser.parse_args(argv)
    root = backup_root()
    if args.list:
        sets = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
        if not sets:
            print(f"No hay respaldos en {root}")
        for folder in sets:
            try:
                files = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["files"]
                print(f"{folder}  ({', '.join(item['name'] for item in files)})")
            except (OSError, ValueError, KeyError):
                print(f"{folder}  (incompleto)")
        return 0
    try:
        folder = backup_all("manual", include_knowledge=args.include_knowledge)
    except (OSError, sqlite3.Error, device_store.BackupError) as error:
        print(f"El respaldo fallo: {error}")
        return 3
    files = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["files"]
    print(f"Respaldo creado en {folder}")
    for item in files:
        print(f"  {item['name']}  {item['bytes']} bytes  esquema v{item['schema_version']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
