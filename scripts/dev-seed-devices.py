r"""Fill a SEPARATE device database with fake devices to review the admin panel by eye.

Never touches the real registry. Usage (from the project root, in the venv):

    .venv\Scripts\python scripts\dev-seed-devices.py            # 9 sample devices
    .venv\Scripts\python scripts\dev-seed-devices.py --many     # 20 devices

Then start the backend pointing at the printed path (see the output).
Fake rows are marked with key_id "dev-seed:*"; running again replaces them.
"""
import argparse
import os
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.pairing import store  # noqa: E402

SEED_DB = ROOT / "storage" / "pairing" / "dev-seed-devices.sqlite3"
MARK = "dev-seed:"


def real_devices(path):
    """Rows that this script did not create. A missing file or table counts as none."""
    if not path.is_file():
        return 0
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
        try:
            return db.execute("SELECT COUNT(*) FROM installs WHERE key_id NOT LIKE ?", (MARK + "%",)).fetchone()[0]
        except sqlite3.OperationalError:
            return 0


def refuse(message):
    sys.exit("dev-seed-devices: " + message + " Nothing was written.")


def samples(now, many):
    base = dict(variant="phone", code=None, approved_at=None, last_seen=None, last_ip=None, expires=now + 300)
    rows = [
        dict(base, name="Pixel de prueba", model="Google Pixel 8", state="pending", code="482913",
             created_at=now - 40, last_seen=now - 40, last_ip="192.168.50.21"),
        dict(base, name='<script>alert("xss")</script>', model="<img src=x onerror=alert(1)>", state="pending",
             code="105377", created_at=now - 20, last_seen=now - 20, last_ip="192.168.50.22"),
        dict(base, name="Moto G de almacén", model="Motorola moto g54", state="approved", code="770021",
             created_at=now - 90, approved_at=now - 10, last_seen=now - 90, last_ip="192.168.50.23"),
        dict(base, name="Samsung de Ana", model="Samsung Galaxy A54", state="active",
             created_at=now - 86400 * 3, approved_at=now - 86400 * 3, last_seen=now - 120, last_ip="192.168.50.24"),
        dict(base, name="Lentes recepción", model="Ray-Ban Meta", variant="meta", state="active",
             created_at=now - 86400, approved_at=now - 86400, last_seen=now - 3 * 3600, last_ip="192.168.50.25"),
        dict(base, name=None, model=None, state="active",
             created_at=now - 86400 * 9, approved_at=now - 86400 * 9, last_seen=now - 86400 * 2, last_ip="192.168.50.26"),
        dict(base, name="Celular perdido", model="Xiaomi Redmi Note 13", state="blocked",
             created_at=now - 86400 * 20, approved_at=now - 86400 * 20, last_seen=now - 86400 * 6, last_ip="192.168.50.27"),
        dict(base, name="Solicitud desconocida", model="Generic Android", state="denied", code="913540",
             created_at=now - 60, last_seen=now - 60, last_ip="192.168.50.28"),
        dict(base, name="Nunca conectado", model="Google Pixel 7a", state="active",
             created_at=now - 86400 * 30, approved_at=now - 86400 * 30),
    ]
    if many:
        for n in range(20 - len(rows)):
            rows.append(dict(base, name=f"Celular de pruebas {n + 1:02d}", model="Google Pixel 6a", state="active",
                             created_at=now - 86400 * (n + 2), approved_at=now - 86400 * (n + 2),
                             last_seen=now - 300 * (n + 1), last_ip=f"192.168.51.{n + 10}"))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--many", action="store_true", help="create 20 devices to check the layout at scale")
    args = parser.parse_args()

    target = SEED_DB.resolve()
    if target == store.DEFAULT_DB.resolve():
        refuse("the seed path is the real registry.")
    configured = os.environ.get("ITGLASSES_PAIRING_DB", "").strip()
    if configured and real_devices(Path(configured)):
        refuse(f"ITGLASSES_PAIRING_DB points to {configured}, which contains real devices.")
    if real_devices(target):
        refuse(f"{target} contains devices this script did not create.")

    store.ensure_database(target)
    now = int(time.time())
    rows = samples(now, args.many)
    with closing(sqlite3.connect(target)) as db, db:
        db.execute("DELETE FROM installs WHERE key_id LIKE ?", (MARK + "%",))
        for n, row in enumerate(rows):
            db.execute(
                "INSERT INTO installs(id,key_id,variant,code,state,expires,name,model,created_at,approved_at,"
                "last_seen,last_ip) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"dev-seed-{n:02d}", f"{MARK}{n:02d}", row["variant"], row["code"], row["state"], row["expires"],
                 row["name"], row["model"], row["created_at"], row["approved_at"], row["last_seen"], row["last_ip"]))
    print(f"Created {len(rows)} fake devices in {target}")
    print("Pending, approved and denied samples expire in 5 minutes; run this script again to recreate them.")
    print("Start the backend with it (PowerShell):")
    print(f'  $env:ITGLASSES_PAIRING_DB = "{target}"; .\\scripts\\start-backend.ps1')


if __name__ == "__main__":
    main()
