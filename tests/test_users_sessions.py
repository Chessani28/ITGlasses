"""Stage 3 (usuarios): server-side web sessions, login/logout/me/password, lockout, Origin and CSRF checks."""
import importlib.util
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.pairing import store
from backend.app.providers.mock import MockLLM
from backend.app.users import accounts, audit, passwords, sessions

LOCAL = "http://127.0.0.1:8000"
PAGE = {"Origin": LOCAL, "Sec-Fetch-Site": "same-origin"}
PW = "clave-de-prueba"
NEW = "clave-nueva-de-prueba"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "devices.sqlite3"
    monkeypatch.setenv("ITGLASSES_PAIRING_DB", str(path))
    monkeypatch.setattr(store, "_ready", set())
    for name in (passwords.MIN_LENGTH_VARIABLE, sessions.IDLE_VARIABLE, sessions.MAX_VARIABLE):
        monkeypatch.delenv(name, raising=False)
    with accounts.connect(path) as db:
        ids = {"root": accounts.create_user(db, "root", PW, "superadmin"),
               "ana": accounts.create_user(db, "ana", PW, "user")}
    return path, ids


def make_client(base_url=LOCAL, address="127.0.0.1"):
    return TestClient(create_app(MockLLM()), base_url=base_url, client=(address, 50000))


@pytest.fixture
def client(env):
    with make_client() as test_client:
        yield test_client


def login(client, username="ana", password=PW, headers=None):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password},
                           headers=PAGE if headers is None else headers)
    client.cookies.clear()  # every request below sends the cookie it means explicitly
    return response


def token_of(response):
    found = re.search(r"itg_session=([^;]*)", response.headers.get("set-cookie", ""))
    return found.group(1) if found else None


def with_session(token, csrf=None, page=False):
    headers = {"Cookie": f"itg_session={token}", **(PAGE if page or csrf else {})}
    if csrf:
        headers["X-CSRF-Token"] = csrf
    return headers


def session_for(client, username="ana"):
    response = login(client, username)
    assert response.status_code == 200, response.text
    return token_of(response), response.json()["csrf_token"]


def db_rows(path, sql, parameters=()):
    with closing(sqlite3.connect(path)) as db:
        return db.execute(sql, parameters).fetchall()


def actions(path):
    with accounts.connect(path) as db:
        return [(e["action"], e["outcome"], (e["detail"] or {}).get("reason")) for e in audit.entries(db)]


# --- login ------------------------------------------------------------------------------------------------

def test_login_sets_a_strict_cookie_and_stores_only_a_digest(env, client):
    path, ids = env
    response = login(client, "ANA")
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    for flag in ("httponly", "secure", "samesite=strict", "path=/", "max-age=43200"):
        assert flag in cookie
    body = response.json()
    assert body["user"] == {"id": ids["ana"], "username": "ana", "display_name": "ana", "role": "user",
                            "must_change_pw": False}
    assert body["password_min_length"] == 5 and len(body["csrf_token"]) == 64
    token = token_of(response)
    data = path.read_bytes()
    assert token.encode() not in data and body["csrf_token"].encode() not in data
    assert db_rows(path, "SELECT COUNT(*) FROM web_sessions WHERE user_id=?", (ids["ana"],)) == [(1,)]
    assert actions(path)[0] == ("auth.login", "ok", None)


def test_me_needs_a_session(client):
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/auth/me").json()["error"]["code"] == "not_authenticated"
    token, csrf = session_for(client)
    response = client.get("/api/v1/auth/me", headers=with_session(token))
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["user"]["username"] == "ana" and response.json()["csrf_token"] == csrf


def test_every_failure_gets_the_same_answer(env, client):
    path, ids = env
    with accounts.connect(path) as db:
        accounts.create_user(db, "beto", PW, "user")
        accounts.set_status(db, accounts.find(db, "beto")["id"], "disabled")
    answers = [login(client, "nadie", PW), login(client, "ana", "incorrecta"), login(client, "beto", PW),
               login(client, "x" * 100, PW)]
    assert {r.status_code for r in answers} == {401}
    assert len({r.text for r in answers}) == 1
    assert "incorrectos" in answers[0].json()["error"]["message"]
    assert [token_of(r) for r in answers] == [None] * 4
    reasons = [reason for action, outcome, reason in actions(path) if action == "auth.login"]
    assert reasons == ["unknown_user", "disabled", "bad_password", "unknown_user"]


def test_a_mistyped_password_in_the_user_field_is_not_stored(env, client):
    path, _ = env
    login(client, "mi-clave-secreta", PW)
    assert b"mi-clave-secreta" not in path.read_bytes()


def test_lockout_after_5_failures_then_release(env, client):
    path, ids = env
    for _ in range(4):
        login(client, "ana", "incorrecta")
    assert db_rows(path, "SELECT locked_until FROM users WHERE id=?", (ids["ana"],)) == [(None,)]
    login(client, "ana", "incorrecta")
    [(locked,)] = db_rows(path, "SELECT locked_until FROM users WHERE id=?", (ids["ana"],))
    assert 14 * 60 < locked - time.time() <= 15 * 60
    blocked = login(client, "ana", PW)  # the right password does not open a locked account
    assert blocked.status_code == 401 and token_of(blocked) is None
    assert ("auth.locked", "ok", None) in actions(path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE users SET locked_until=? WHERE id=?", (int(time.time()) - 1, ids["ana"]))
    assert login(client, "ana", PW).status_code == 200
    assert db_rows(path, "SELECT failed_count,locked_until FROM users WHERE id=?", (ids["ana"],)) == [(0, None)]


@pytest.mark.parametrize(("failures", "seconds"), [(4, None), (5, 900), (6, None), (10, 1800), (15, 3600),
                                                   (500, 86400)])
def test_lock_doubles_up_to_a_day(failures, seconds):
    assert sessions._lock_for(failures) == seconds


def test_too_many_failures_from_one_address_are_refused(env, client):
    for number in range(sessions.LOGIN_IP_FAILURES):
        assert login(client, f"nadie{number}", PW).status_code == 401
    refused = login(client, "ana", PW)
    assert refused.status_code == 429 and refused.json()["error"]["code"] == "login_rate_limited"


def test_a_new_login_closes_the_previous_session_of_this_browser(client):
    old, _ = session_for(client)
    response = client.post("/api/v1/auth/login", json={"username": "ana", "password": PW},
                           headers={**PAGE, "Cookie": f"itg_session={old}"})
    client.cookies.clear()
    assert response.status_code == 200
    assert client.get("/api/v1/auth/me", headers=with_session(old)).status_code == 401
    assert client.get("/api/v1/auth/me", headers=with_session(token_of(response))).status_code == 200


# --- where requests may come from -------------------------------------------------------------------------

@pytest.mark.parametrize("headers", [{}, {"Origin": "http://evil.test"}, {"Origin": "null"},
                                     {"Origin": LOCAL, "Sec-Fetch-Site": "cross-site"},
                                     {"Origin": LOCAL, "Sec-Fetch-Site": "same-site"},
                                     {"Origin": "https://172.16.5.142:8443"}])
def test_login_only_from_the_backend_page(client, headers):
    response = login(client, headers=headers)
    assert response.status_code == 403 and response.json()["error"]["code"] == "origin_forbidden"


def test_web_routes_only_from_this_pc(env):
    with make_client(address="192.168.1.50") as remote:
        assert login(remote).status_code == 403
    with make_client(base_url="http://evil.test") as rebound:
        assert login(rebound, headers={"Origin": "http://evil.test"}).status_code == 403
    with make_client() as proxied:
        assert login(proxied, headers={**PAGE, "X-Forwarded-For": "10.0.0.9"}).status_code == 403
    for host in ("localhost", "[::1]"):
        with make_client(base_url=f"http://{host}:8000") as local:
            ok = login(local, headers={"Origin": f"http://{host}:8000"})
            assert ok.status_code == 200, host


def test_caddy_never_publishes_the_login_routes():
    spec = importlib.util.spec_from_file_location("caddy_config", ROOT / "scripts" / "caddy-config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert not [path for _, path in module.phone_routes() if path.startswith("/api/v1/auth")]


# --- logout, CSRF and expiry ------------------------------------------------------------------------------

def test_logout_needs_the_csrf_token_and_ends_the_session(env, client):
    path, _ = env
    token, csrf = session_for(client)
    assert client.post("/api/v1/auth/logout", headers=with_session(token, page=True)).status_code == 403
    assert client.post("/api/v1/auth/logout", headers=with_session(token, "0" * 64)).status_code == 403
    response = client.post("/api/v1/auth/logout", headers=with_session(token, csrf))
    assert response.status_code == 200 and "max-age=0" in response.headers["set-cookie"].lower()
    assert client.get("/api/v1/auth/me", headers=with_session(token)).status_code == 401
    assert actions(path)[0] == ("auth.logout", "ok", None)
    assert client.post("/api/v1/auth/logout", headers=PAGE).status_code == 200  # no session: nothing to do


def test_idle_and_absolute_expiry(env, client):
    path, _ = env
    token, _ = session_for(client)
    now = int(time.time())
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE web_sessions SET idle_expires=?", (now - 1,))
    assert client.get("/api/v1/auth/me", headers=with_session(token)).status_code == 401
    token, _ = session_for(client)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE web_sessions SET absolute_expires=? WHERE revoked_at IS NULL AND idle_expires>?",
                   (now - 1, now))
    assert client.get("/api/v1/auth/me", headers=with_session(token)).status_code == 401


def test_activity_slides_the_idle_expiry_but_never_past_the_absolute_one(env, client):
    path, _ = env
    token, _ = session_for(client)
    now = int(time.time())
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE web_sessions SET last_seen=?, idle_expires=?, absolute_expires=?",
                   (now - 600, now + 60, now + 300))
    body = client.get("/api/v1/auth/me", headers=with_session(token)).json()
    assert body["session"]["idle_expires"] == now + 300 or abs(body["session"]["idle_expires"] - (now + 300)) <= 2
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE web_sessions SET last_seen=?, idle_expires=?, absolute_expires=?",
                   (now - 600, now + 60, now + 9999))
    body = client.get("/api/v1/auth/me", headers=with_session(token)).json()
    assert abs(body["session"]["idle_expires"] - (now + 1800)) <= 2


def test_disabling_a_user_ends_their_sessions(env, client):
    path, ids = env
    token, _ = session_for(client)
    with accounts.connect(path) as db:
        accounts.set_status(db, ids["ana"], "disabled")
    assert client.get("/api/v1/auth/me", headers=with_session(token)).status_code == 401


# --- password change --------------------------------------------------------------------------------------

def change(client, token, csrf, current=PW, new=NEW):
    response = client.post("/api/v1/auth/password", json={"current_password": current, "new_password": new},
                           headers=with_session(token, csrf))
    client.cookies.clear()
    return response


def test_password_change_renews_the_session_and_retires_the_old_password(env, client):
    path, ids = env
    token, csrf = session_for(client)
    other, _ = session_for(client)  # another browser of the same user
    response = change(client, token, csrf)
    assert response.status_code == 200
    renewed = token_of(response)
    assert renewed and renewed != token
    for old in (token, other):
        assert client.get("/api/v1/auth/me", headers=with_session(old)).status_code == 401
    assert client.get("/api/v1/auth/me", headers=with_session(renewed)).status_code == 200
    assert login(client, "ana", PW).status_code == 401 and login(client, "ana", NEW).status_code == 200
    assert ("user.password_change", "ok", None) in actions(path)


def test_password_change_checks(env, client):
    path, ids = env
    token, csrf = session_for(client)
    wrong = change(client, token, csrf, current="no-es-esta")
    assert wrong.status_code == 403 and wrong.json()["error"]["code"] == "current_password_wrong"
    short = change(client, token, csrf, new="abcd")
    assert short.status_code == 422 and "entre 5 y 256" in short.json()["error"]["message"]
    assert client.post("/api/v1/auth/password", json={"current_password": PW, "new_password": NEW},
                       headers=with_session(token, page=True)).status_code == 403  # no CSRF token
    assert client.post("/api/v1/auth/password", json={"current_password": PW, "new_password": NEW, "x": 1},
                       headers=with_session(token, csrf)).status_code == 422


def test_guessing_the_current_password_locks_the_account_and_its_sessions(env, client):
    path, ids = env
    token, csrf = session_for(client)
    for _ in range(sessions.MAX_FAILURES):
        change(client, token, csrf, current="adivinando")
    assert client.get("/api/v1/auth/me", headers=with_session(token)).status_code == 401
    assert login(client, "ana", PW).status_code == 401  # locked


def test_a_forced_change_is_reported_and_cleared(env, client):
    path, ids = env
    with accounts.connect(path) as db:
        accounts.set_password(db, ids["ana"], PW, must_change=True)
    token, csrf = session_for(client)
    assert client.get("/api/v1/auth/me", headers=with_session(token)).json()["user"]["must_change_pw"] is True
    renewed = token_of(change(client, token, csrf))
    assert client.get("/api/v1/auth/me", headers=with_session(renewed)).json()["user"]["must_change_pw"] is False


def test_long_inputs_are_refused_before_hashing(client):
    response = client.post("/api/v1/auth/login", json={"username": "ana", "password": "x" * 1025}, headers=PAGE)
    assert response.status_code == 422 and "formulario" in response.json()["error"]["message"]


# --- settings ---------------------------------------------------------------------------------------------

def test_session_lifetimes_are_configurable(env, monkeypatch):
    monkeypatch.setenv(sessions.MAX_VARIABLE, "2")
    monkeypatch.setenv(sessions.IDLE_VARIABLE, "10")
    assert sessions.lifetimes() == (600, 7200)
    with make_client() as configured:
        assert "max-age=7200" in login(configured).headers["set-cookie"].lower()


@pytest.mark.parametrize(("idle", "maximum"), [("4", None), ("1441", None), ("abc", None), (None, "0"),
                                               (None, "169"), ("120", "1")])
def test_invalid_session_settings_stop_the_backend(env, monkeypatch, idle, maximum):
    if idle is not None:
        monkeypatch.setenv(sessions.IDLE_VARIABLE, idle)
    if maximum is not None:
        monkeypatch.setenv(sessions.MAX_VARIABLE, maximum)
    with pytest.raises(sessions.SessionConfigError):
        sessions.lifetimes()
    with pytest.raises(sessions.SessionConfigError):
        with make_client():
            pass
