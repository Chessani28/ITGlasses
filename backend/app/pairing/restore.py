"""Restore the device database from a backup in storage/pairing/backups/ (revert of a schema migration).

Run it on this PC with the backend STOPPED (scripts\stop-backend.ps1):

    .venv\Scripts\python -m backend.app.pairing.restore --list
    .venv\Scripts\python -m backend.app.pairing.restore storage\pairing\backups\devices-v1-....sqlite3

The current database is copied to backups/ (label "pre-restore") before it is replaced, so a restore can be
undone too. A restored older schema is migrated again by the new code at the next start: to stay on the old
version also switch back to the old code (e.g. the tag voz-rapida-v3).
"""
import argparse
import socket
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from backend.app.core.config import settings
from . import store

CONFIRMATION = "RESTAURAR"


class RestoreError(RuntimeError):
    """The backup cannot be restored (missing, damaged, not a device database or too new)."""


def _read_only(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def inspect_backup(path):
    """Schema version of a valid device-database backup; RestoreError otherwise."""
    path = Path(path)
    if not path.is_file():
        raise RestoreError("No existe el archivo de respaldo")
    try:
        with closing(_read_only(path)) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RestoreError("El respaldo esta danado (integrity_check)")
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "installs" not in tables:
                raise RestoreError("El archivo no es una base de dispositivos de ITGlasses")
            version = db.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.DatabaseError as error:
        raise RestoreError("El archivo no es una base SQLite valida") from error
    if version > store.SCHEMA_VERSION:
        raise RestoreError(f"El respaldo es de una version mas nueva ({version}) que este codigo "
                           f"({store.SCHEMA_VERSION})")
    return version


def backend_running(port=None):
    """True when something answers on the backend port of this PC (the restore refuses to run then)."""
    port = settings.port if port is None else port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def list_backups(target=None):
    """[(path, version or None)] of the backups next to the database, newest first."""
    folder = store.backup_dir(target or store.database_path())
    if not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.glob("*.sqlite3"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            found.append((path, inspect_backup(path)))
        except RestoreError:
            found.append((path, None))
    return found


def restore(backup, target=None):
    """Replace the device database with ``backup``. Returns (restored version, safety copy of the old one or None)."""
    backup = Path(backup).resolve()
    target = Path(target or store.database_path()).resolve()
    if backup == target:
        raise RestoreError("El respaldo y la base activa son el mismo archivo")
    version = inspect_backup(backup)
    safety = None
    if target.is_file() and target.stat().st_size > 0:
        with closing(sqlite3.connect(target, timeout=5)) as current:
            current_version = current.execute("PRAGMA user_version").fetchone()[0]
            safety = store.backup_database(current, target, current_version, label="pre-restore")
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(_read_only(backup)) as source, closing(sqlite3.connect(target, timeout=5)) as destination:
        source.backup(destination)
    with closing(_read_only(target)) as check:
        if (check.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or check.execute("PRAGMA user_version").fetchone()[0] != version):
            raise RestoreError("La base restaurada no paso la verificacion; usa la copia pre-restore")
    with store._ready_lock:
        store._ready.discard(target)
    return version, safety


def main(argv=None, ask=input):
    parser = argparse.ArgumentParser(prog="python -m backend.app.pairing.restore",
                                     description="Restaura la base de dispositivos desde un respaldo.")
    parser.add_argument("backup", nargs="?", help="archivo de respaldo (storage/pairing/backups/...)")
    parser.add_argument("--list", action="store_true", help="lista los respaldos disponibles")
    parser.add_argument("--db", help="base a restaurar (por defecto la configurada)")
    parser.add_argument("--yes", action="store_true", help="no pedir confirmacion")
    args = parser.parse_args(argv)
    target = Path(args.db) if args.db else store.database_path()
    if args.list or not args.backup:
        backups = list_backups(target)
        if not backups:
            print(f"No hay respaldos en {store.backup_dir(target)}")
        for path, version in backups:
            print(f"v{version if version is not None else '?'}  {path}")
        return 0 if args.list else 1
    if backend_running():
        print("El backend esta corriendo. Detenlo primero (scripts\\stop-backend.ps1) y vuelve a intentar.")
        return 2
    try:
        version = inspect_backup(args.backup)
    except RestoreError as error:
        print(f"No se restauro nada: {error}")
        return 3
    print(f"Base activa: {target}\nRespaldo:    {Path(args.backup).resolve()} (esquema v{version})")
    if not args.yes and ask(f"Escribe {CONFIRMATION} para reemplazar la base activa: ").strip() != CONFIRMATION:
        print("Cancelado; no se cambio nada.")
        return 1
    try:
        version, safety = restore(args.backup, target)
    except (RestoreError, store.BackupError, OSError, sqlite3.Error) as error:
        print(f"La restauracion fallo: {error}")
        return 3
    if safety:
        print(f"Copia de la base anterior: {safety}")
    print(f"Restaurada la base de dispositivos (esquema v{version}).")
    if version < store.SCHEMA_VERSION:
        print("Este codigo la volvera a migrar al arrancar. Para quedarte en la version anterior vuelve tambien "
              "al codigo anterior (por ejemplo: git switch --detach voz-rapida-v3).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
