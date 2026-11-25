"""Self-registration (ITGLASSES_REGISTRATION = off | approval | open): role always "user", unique names across
users and pending requests without telling which exist, pending accounts that cannot log in, approval and
rejection by an admin, rate limit per real client address, audit, and the database staying on schema v2."""
import importlib.util
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from backend.app.main import create_app
from backend.app.pairing import store
from backend.app.providers.mock import MockLLM
from backend.app.users import accounts, audit, passwords, registration
from test_users_access import ENVIRONMENT, LOCAL, PAGE, PW, sign_in

ROOT = "/api/v1"
REGISTER = ROOT + "/auth/register"
LOGIN = ROOT + "/auth/login"
PENDING = ROOT + "/admin/users/pending"
NEW_PW = "una-clave-larga"


@pytest.fixture
def world(tmp_path, monkeypatch):
    path = tmp_path / "devices.sqlite3"
    monkeypatch.setenv("ITGLASSES_PAIRING_DB", str(path))
    monkeypatch.setattr(store, "_ready", set())
    for name in (*ENVIRONMENT, registration.MODE_VARIABLE):
        monkeypatch.delenv(name, raising=False)
    ids = {}
    with accounts.connect(path) as db:
        for username, role in (("root", "superadmin"), ("adm", "admin"), ("ana", "user")):
            ids[username] = accounts.create_user(db, username, PW, role)
    with TestClient(create_app(MockLLM()), base_url=LOCAL, client=("127.0.0.1", 50000)) as client:
        yield client, path, ids


def register(client, username="nuevo", password=NEW_PW, repeat=None, **extra):
    body = {"username": username, "password": password, "password_repeat": repeat or password, **extra}
    response = client.post(REGISTER, json=body, headers=PAGE)
    client.cookies.clear()
    return response


def code(response):
    return response.status_code, (response.json().get("error") or {}).get("code")


def login(client, username, password=NEW_PW):
    response = client.post(LOGIN, json={"username": username, "password": password}, headers=PAGE)
    client.cookies.clear()
    return response


def rows(path, sql, *args):
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(sql, args)]


def entries(path, action):
    with accounts.connect(path) as db:
        return [entry for entry in audit.entries(db, limit=500) if entry["action"] == action]


# --- mode and config ---------------------------------------------------------------------------------------

def test_the_default_mode_is_approval_and_the_login_screen_is_told(world):
    client, _, _ = world
    assert registration.mode() == "approval"
    assert client.get(ROOT + "/auth/config").json()["registration"] == "approval"


@pytest.mark.parametrize("value", ["closed", "yes", "1"])
def test_an_invalid_mode_refuses_to_start(monkeypatch, value):
    monkeypatch.setenv(registration.MODE_VARIABLE, value)
    with pytest.raises(Exception, match=registration.MODE_VARIABLE):
        registration.mode()


def test_off_hides_the_link_and_the_endpoint(world, monkeypatch):
    client, path, _ = world
    monkeypatch.setenv(registration.MODE_VARIABLE, "off")
    assert client.get(ROOT + "/auth/config").json()["registration"] == "off"
    assert register(client).status_code == 404
    assert rows(path, "SELECT * FROM registrations") == []
    with accounts.connect(path) as db:
        assert accounts.find(db, "nuevo") is None


# --- approval ------------------------------------------------------------------------------------------------

def test_a_request_waits_for_approval_and_cannot_log_in(world):
    client, path, _ = world
    response = register(client, "Nuevo", display_name="Nuevo Pérez")
    assert response.status_code == 202 and response.json()["status"] == "pending"
    assert "set-cookie" not in response.headers
    [row] = rows(path, "SELECT * FROM registrations")
    assert (row["username"], row["display_name"]) == ("nuevo", "Nuevo Pérez")
    assert row["expires_at"] - row["created_at"] == registration.PENDING_DAYS * 86400
    assert NEW_PW.encode() not in path.read_bytes()
    with accounts.connect(path) as db:
        assert accounts.find(db, "nuevo") is None        # not a user until approved
    # Only the right password learns that the account is pending; a wrong one gets the generic answer.
    assert code(login(client, "nuevo")) == (403, "account_pending")
    assert login(client, "nuevo").json()["error"]["message"] == "Tu cuenta está pendiente de aprobación."
    assert code(login(client, "nuevo", "otra-clave-x")) == (401, "login_failed")
    [entry] = entries(path, "user.register")
    assert entry["outcome"] == "ok" and entry["detail"]["username"] == "nuevo"
    assert NEW_PW not in str(entries(path, "user.register"))


def test_a_duplicate_gets_the_same_answer_and_creates_nothing(world):
    client, path, _ = world
    first = register(client, "nuevo")
    taken_user = register(client, "ANA", password="cualquier-clave")       # exists in users (any case)
    taken_request = register(client, "nuevo", password="cualquier-clave")  # exists in registrations
    assert first.status_code == taken_user.status_code == taken_request.status_code == 202
    assert first.json() == taken_user.json() == taken_request.json()
    assert [row["username"] for row in rows(path, "SELECT username FROM registrations")] == ["nuevo"]
    # The first request keeps its own password.
    assert code(login(client, "nuevo")) == (403, "account_pending")
    assert [entry["outcome"] for entry in entries(path, "user.register")].count("denied") == 2


def test_a_short_password_or_a_mismatch_is_refused(world, monkeypatch):
    client, path, _ = world
    monkeypatch.setenv(passwords.MIN_LENGTH_VARIABLE, "10")
    assert code(register(client, password="corta-9ch")) == (422, "password_length")
    assert code(register(client, password=NEW_PW, repeat=NEW_PW + "x")) == (422, "password_mismatch")
    assert code(register(client, username="a")) == (422, "username_invalid")
    assert rows(path, "SELECT * FROM registrations") == []


@pytest.mark.parametrize("extra", [{"role": "admin"}, {"role": "superadmin"}, {"role": "user"},
                                   {"status": "active"}, {"must_change_pw": False}])
def test_the_body_cannot_choose_a_role_or_anything_else(world, monkeypatch, extra):
    client, path, _ = world
    for mode in ("approval", "open"):
        monkeypatch.setenv(registration.MODE_VARIABLE, mode)
        assert register(client, "colado", **extra).status_code == 422
    assert rows(path, "SELECT * FROM registrations") == []
    assert rows(path, "SELECT username FROM users WHERE username='colado'") == []
    assert rows(path, "SELECT COUNT(*) AS n FROM users WHERE role IN ('admin','superadmin')") == [{"n": 2}]


def test_an_admin_approves_atomically_with_the_same_hash(world):
    client, path, ids = world
    register(client, "nuevo", display_name="Nuevo")
    [request] = rows(path, "SELECT * FROM registrations")
    admin = sign_in(client, "adm")
    listed = client.get(PENDING, headers=admin)
    assert listed.status_code == 200 and listed.headers["cache-control"] == "no-store"
    [shown] = listed.json()["pending"]
    assert set(shown) == {"id", "username", "display_name", "created_at", "expires_at"}   # never the hash
    response = client.post(f"{PENDING}/{request['id']}/approve", headers=admin)
    assert response.status_code == 200
    user = response.json()["user"]
    assert (user["username"], user["role"], user["status"], user["must_change_pw"]) == ("nuevo", "user", "active",
                                                                                         False)
    [stored] = rows(path, "SELECT pw_salt,pw_hash,pw_params,created_by FROM users WHERE username='nuevo'")
    assert (stored["pw_salt"], stored["pw_hash"], stored["pw_params"]) == \
        (request["pw_salt"], request["pw_hash"], request["pw_params"])        # copied, not re-hashed
    assert stored["created_by"] == ids["adm"]
    assert rows(path, "SELECT * FROM registrations") == []
    assert login(client, "nuevo").status_code == 200
    [entry] = entries(path, "user.approve")
    assert (entry["actor_user_id"], entry["target_id"], entry["detail"]["username"]) == (ids["adm"], user["id"],
                                                                                          "nuevo")
    assert code(client.post(f"{PENDING}/{request['id']}/approve", headers=admin)) == (404, "registration_not_found")


def test_approval_is_all_or_nothing(world):
    client, path, _ = world
    register(client, "nuevo")
    [request] = rows(path, "SELECT id FROM registrations")
    with accounts.connect(path) as db:     # someone took the name in users meanwhile (e.g. an admin)
        accounts.create_user(db, "nuevo", PW, "user")
    admin = sign_in(client, "adm")
    assert code(client.post(f"{PENDING}/{request['id']}/approve", headers=admin)) == (409, "username_taken")
    assert len(rows(path, "SELECT id FROM registrations")) == 1     # nothing half done


def test_an_admin_rejects_and_the_name_is_free_again(world):
    client, path, ids = world
    register(client, "nuevo")
    [request] = rows(path, "SELECT id FROM registrations")
    root = sign_in(client, "root")
    assert client.post(f"{PENDING}/{request['id']}/reject", headers=root).status_code == 200
    assert rows(path, "SELECT * FROM registrations") == []
    assert code(login(client, "nuevo")) == (401, "login_failed")
    [entry] = entries(path, "user.reject")
    assert (entry["actor_user_id"], entry["target_id"], entry["detail"]) == (ids["root"], request["id"],
                                                                              {"username": "nuevo"})
    assert register(client, "nuevo").status_code == 202


def test_only_admins_see_and_decide_pending_requests(world):
    client, path, _ = world
    register(client, "nuevo")
    [request] = rows(path, "SELECT id FROM registrations")
    ana = sign_in(client, "ana")
    assert client.get(PENDING, headers=ana).status_code == 403
    assert client.post(f"{PENDING}/{request['id']}/approve", headers=ana).status_code == 403
    assert client.get(PENDING).status_code == 401
    admin = sign_in(client, "adm")
    no_csrf = {key: value for key, value in admin.items() if key != "X-CSRF-Token"}
    assert client.post(f"{PENDING}/{request['id']}/approve", headers=no_csrf).status_code == 403


def test_pending_requests_expire(world):
    client, path, _ = world
    register(client, "viejo")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE registrations SET expires_at=1")
    assert code(login(client, "viejo")) == (401, "login_failed")
    admin = sign_in(client, "adm")
    assert client.get(PENDING, headers=admin).json()["pending"] == []
    assert rows(path, "SELECT * FROM registrations") == []


# --- open -----------------------------------------------------------------------------------------------------

def test_open_mode_logs_in_at_once_as_a_user(world, monkeypatch):
    client, path, _ = world
    monkeypatch.setenv(registration.MODE_VARIABLE, "open")
    response = register(client, "libre")
    assert response.status_code == 201 and "itg_session=" in response.headers["set-cookie"]
    assert response.json()["user"]["role"] == "user" and response.json()["csrf_token"]
    assert login(client, "libre").status_code == 200
    assert code(register(client, "libre", password="otra-clave-larga")) == (409, "register_failed")
    assert code(register(client, "ADM", password="otra-clave-larga")) == (409, "register_failed")
    assert rows(path, "SELECT * FROM registrations") == []


# --- gate and rate limit ----------------------------------------------------------------------------------------

def test_register_needs_this_pcs_page(world):
    client, path, _ = world
    body = {"username": "x-nuevo", "password": NEW_PW, "password_repeat": NEW_PW}
    assert client.post(REGISTER, json=body).status_code == 403                                      # no Origin
    assert client.post(REGISTER, json=body, headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.post(REGISTER, json=body, headers={**PAGE, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    with TestClient(client.app, base_url=LOCAL, client=("192.168.50.20", 50000)) as outside:
        assert outside.post(REGISTER, json=body, headers=PAGE).status_code == 403
    assert rows(path, "SELECT * FROM registrations") == []


def test_five_requests_per_hour_per_address(world):
    client, path, _ = world
    for number in range(registration.RATE_PER_IP):
        assert register(client, f"persona{number}").status_code == 202
    assert code(register(client, "persona9")) == (429, "register_rate_limited")
    assert len(rows(path, "SELECT * FROM registrations")) == registration.RATE_PER_IP


def test_a_fake_forwarded_for_does_not_evade_the_limit(world):
    """Behind uvicorn's ProxyHeadersMiddleware (trusted proxy 127.0.0.1 only, as backend/run.py configures it): a
    forged X-Forwarded-For neither changes the address the limit counts nor gets past the local-page gate."""
    client, path, _ = world
    proxied = ProxyHeadersMiddleware(client.app, trusted_hosts="127.0.0.1")
    for number in range(registration.RATE_PER_IP):
        assert register(client, f"persona{number}").status_code == 202
    body = {"username": "evasor", "password": NEW_PW, "password_repeat": NEW_PW}
    # From this PC with a different address in X-Forwarded-For each time: refused, never a new budget.
    for fake in ("203.0.113.7", "198.51.100.1"):
        with TestClient(proxied, base_url=LOCAL, client=("127.0.0.1", 50001)) as local:
            assert local.post(REGISTER, json=body, headers={**PAGE, "X-Forwarded-For": fake}).status_code in (403, 429)
    # From another machine claiming to be this PC: the header is ignored (untrusted peer) and the page gate refuses.
    with TestClient(proxied, base_url=LOCAL, client=("192.168.50.20", 50000)) as outside:
        response = outside.post(REGISTER, json=body, headers={**PAGE, "X-Forwarded-For": "127.0.0.1"})
        assert response.status_code == 403
    assert code(register(client, "evasor")) == (429, "register_rate_limited")
    assert rows(path, "SELECT username FROM registrations WHERE username='evasor'") == []
    buckets = {row["bucket"] for row in rows(path, "SELECT bucket FROM limits WHERE bucket LIKE 'register:%'")}
    assert buckets == {"register:127.0.0.1"}


# --- schema --------------------------------------------------------------------------------------------------

def test_the_database_stays_v2_and_is_backed_up_before_the_table_is_added(tmp_path, monkeypatch):
    path = tmp_path / "devices.sqlite3"
    monkeypatch.setattr(store, "_ready", set())
    store.ensure_database(path)
    with closing(sqlite3.connect(path)) as db, db:       # a v2 database as the previous code left it
        db.execute("DROP TABLE registrations")
        db.execute("INSERT INTO users(id,username,display_name,role,pw_salt,pw_hash,pw_params,created_at,status) "
                   "VALUES('u1','vieja','Vieja','user',x'00',x'00','p',1,'disabled')")
    monkeypatch.setattr(store, "_ready", set())
    store.ensure_database(path)
    assert store.SCHEMA_VERSION == 2
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert store.has_table(db, "registrations")
        assert db.execute("SELECT username,status FROM users").fetchall() == [("vieja", "disabled")]
    [copy] = sorted(store.backup_dir(path).glob("*.sqlite3"))
    assert "-v2-registrations-" in copy.name
    with closing(sqlite3.connect(copy)) as db:
        assert not store.has_table(db, "registrations") and db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    monkeypatch.setattr(store, "_ready", set())
    store.ensure_database(path)                              # no second copy once the table exists
    assert len(list(store.backup_dir(path).glob("*.sqlite3"))) == 1


def _old_store(tag):
    """backend/app/pairing/store.py as an older tag has it, loaded as a module (skipped without git or the tag)."""
    repo = Path(__file__).resolve().parents[1]
    if not shutil.which("git"):
        pytest.skip("git not available")
    shown = subprocess.run(["git", "show", f"{tag}:backend/app/pairing/store.py"], cwd=repo, capture_output=True)
    if shown.returncode != 0:
        pytest.skip(f"tag {tag} not available")
    spec = importlib.util.spec_from_loader(f"old_store_{tag.replace('-', '_')}", loader=None)
    module = importlib.util.module_from_spec(spec)
    # The old code finds the project root from its own path (PROJECT_ROOT = parents[3]).
    module.__file__ = str(repo / "backend" / "app" / "pairing" / "store.py")
    exec(compile(shown.stdout.decode("utf-8"), f"{tag}:store.py", "exec"), module.__dict__)  # noqa: S102
    sys.modules.pop(spec.name, None)
    return module


@pytest.mark.parametrize("tag", ["usuarios-v1", "historial-v1"])
def test_an_older_tag_starts_on_the_database_with_the_new_table(tmp_path, monkeypatch, tag):
    path = tmp_path / "devices.sqlite3"
    monkeypatch.setattr(store, "_ready", set())
    store.ensure_database(path)
    with accounts.connect(path) as db:
        registration.request_account(db, "pendiente", None, NEW_PW)
    old = _old_store(tag)
    assert old.SCHEMA_VERSION == 2
    old.ensure_database(path)                                # no SchemaError, no migration, no copy
    assert not list(store.backup_dir(path).glob("*.sqlite3"))
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("SELECT username FROM registrations").fetchall() == [("pendiente",)]
