"""Metadata of the saved conversations, for checks on this PC: never their content or titles.

    .venv\\Scripts\\python -m backend.app.history.summary
"""
import sqlite3
import sys
import time
from contextlib import closing

from . import store


def rows(path=None):
    path = store.database_path() if path is None else path
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        conversations = db.execute(
            "SELECT id,user_id,channel,device_label,message_count,created_at,last_message_at,deleted_at,closed_at "
            "FROM conversations ORDER BY last_message_at").fetchall()
        sources = dict(db.execute("SELECT source, COUNT(*) FROM messages GROUP BY source").fetchall())
    return conversations, sources


def main(argv=None):
    found = rows()
    if found is None:
        print(f"Todavia no hay historial ({store.database_path()}).")
        return 0
    conversations, sources = found
    stamp = lambda value: time.strftime("%Y-%m-%d %H:%M", time.localtime(value)) if value else "-"
    print(f"{'conversacion':<10} {'canal':<7} {'dispositivo':<18} {'mensajes':>8}  {'ultima':<16} estado")
    for cid, _user, channel, label, count, _created, last, deleted, closed in conversations:
        status = "borrada" if deleted else "cerrada" if closed else "abierta"
        print(f"{cid[:8]:<10} {channel:<7} {(label or '-')[:18]:<18} {count:>8}  {stamp(last):<16} {status}")
    print("Mensajes por origen:", ", ".join(f"{k}={v}" for k, v in sorted(sources.items())) or "ninguno")
    return 0


if __name__ == "__main__":
    sys.exit(main())
