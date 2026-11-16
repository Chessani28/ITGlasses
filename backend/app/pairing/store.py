"""Provision once with python -m backend.app.pairing.store PATH (offline)."""
import getpass
import hashlib
import os
import secrets
import sqlite3
import sys
import time
from contextlib import contextmanager, closing
from pathlib import Path

from fastapi import HTTPException, Request

SCHEMA = """
CREATE TABLE IF NOT EXISTS admin (id INTEGER PRIMARY KEY CHECK(id=1), salt BLOB, digest BLOB);
CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS installs (
 id TEXT PRIMARY KEY, key_id TEXT NOT NULL, variant TEXT NOT NULL, code TEXT,
 state TEXT NOT NULL, expires INTEGER NOT NULL, token TEXT UNIQUE, token_exp INTEGER,
 nonce TEXT, nonce_exp INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS open_code ON installs(code) WHERE code IS NOT NULL;
CREATE INDEX IF NOT EXISTS install_key ON installs(key_id,state);
CREATE TABLE IF NOT EXISTS seen (key_id TEXT, jti TEXT, expires INTEGER, PRIMARY KEY(key_id,jti));
CREATE INDEX IF NOT EXISTS seen_exp ON seen(expires);
CREATE TABLE IF NOT EXISTS limits (bucket TEXT PRIMARY KEY, count INTEGER, expires INTEGER);
"""


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64*1024**2)


def provision(path, password):
    if not 14 <= len(password) <= 256:
        raise ValueError("Use 14 to 256 characters")
    salt = secrets.token_bytes(16)
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO admin VALUES(1,?,?)", (salt, password_hash(password, salt)))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def origin(request: Request):
    configured = os.getenv("ITGLASSES_PAIRING_ORIGIN", "")
    if not configured:
        raise HTTPException(404)
    if request.url.scheme != "https" or configured != f"https://{request.url.netloc}":
        raise HTTPException(403)
    return configured


def permitted_browser_origin(request):
    supplied = request.headers.get("origin")
    if supplied in (None, "http://localhost:8000", "http://127.0.0.1:8000"):
        return True
    return (request.url.scheme == "https" and supplied == os.getenv("ITGLASSES_PAIRING_ORIGIN")
            and supplied == f"https://{request.url.netloc}")


@contextmanager
def transaction():
    path = Path(os.environ.get("ITGLASSES_PAIRING_DB", ""))
    if not path.is_file():
        raise HTTPException(503)
    with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=rw", uri=True, timeout=5)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN IMMEDIATE")
        yield db


def throttle(request, name, per_ip, total, window):
    now = int(time.time())
    peer = request.client.host if request.client else "unknown"
    with transaction() as db:
        db.execute("DELETE FROM limits WHERE expires<=?", (now,))
        for key, maximum in ((name,total), (name+":"+peer,per_ip)):
            row = db.execute("SELECT count FROM limits WHERE bucket=?", (key,)).fetchone()
            if row and row[0] >= maximum:
                raise HTTPException(429, headers={"Retry-After":str(window)})
            db.execute("INSERT INTO limits VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1",
                       (key,now+window))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python -m backend.app.pairing.store DATABASE")
    password = getpass.getpass("New ITGlasses administrator password: ")
    if password != getpass.getpass("Repeat password: "):
        sys.exit("Passwords differ")
    provision(sys.argv[1], password)

