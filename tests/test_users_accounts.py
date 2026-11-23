"""Stage 2 (usuarios): password hashing, accounts, audit log and the local scripts (create-superadmin, reset-access)."""
import sqlite3
from contextlib import closing

import pytest

from backend.app.pairing import store
from backend.app.users import accounts, audit, cli, passwords

GOOD = "correcto-caballo-bateria"
OTHER = "otra-frase-bastante-larga"


@pytest.fixture(autouse=True)
def fresh_process(monkeypatch):
    monkeypatch.setattr(store, "_ready", set())
    monkeypatch.delenv(passwords.MIN_LENGTH_VARIABLE, raising=False)  # the PC's own setting never leaks in


@pytest.fixture
def path(tmp_path):
    return tmp_path / "devices.sqlite3"


def add_device(path, device_id, state, owner=None):
    store.ensure_database(path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO installs(id,key_id,variant,state,expires,user_id) VALUES(?,?,?,?,?,?)",
                   (device_id, "key-" + device_id, "phone", state, 9999999999, owner))


def add_session(db, user_id, digest="s1"):
    db.execute("INSERT INTO web_sessions(token_digest,user_id,csrf_digest,created_at,last_seen,idle_expires,"
               "absolute_expires) VALUES(?,?,?,?,?,?,?)", (digest, user_id, "c", 1, 1, 9, 9))


def open_sessions(db, user_id):
    return db.execute("SELECT COUNT(*) FROM web_sessions WHERE user_id=? AND revoked_at IS NULL",
                      (user_id,)).fetchone()[0]


def secrets_in(path, *values):
    """True if any of the values appears anywhere in the database file."""
    data = path.read_bytes()
    return any(value.encode("utf-8") in data for value in values)


# --- passwords --------------------------------------------------------------------------------------------

def test_hash_and_verify():
    salt, digest, params = passwords.hash_password(GOOD)
    assert params == "scrypt:n=32768,r=8,p=1,dklen=64" and len(salt) == 16 and len(digest) == 64
    assert passwords.verify_password(GOOD, salt, digest, params)
    assert not passwords.verify_password(GOOD + "x", salt, digest, params)
    assert passwords.hash_password(GOOD)[0] != salt  # a new salt every time
    assert not passwords.needs_rehash(params)


@pytest.mark.parametrize("params", ["", "bcrypt:x", "scrypt:n=32768,r=8,p=1", "scrypt:n=1048576,r=8,p=1,dklen=64",
                                    "scrypt:n=30000,r=8,p=1,dklen=64", "scrypt:n=abc,r=8,p=1,dklen=64"])
def test_unreadable_stored_parameters_never_verify(params):
    salt, digest, _ = passwords.hash_password(GOOD)
    assert passwords.verify_password(GOOD, salt, digest, params) is False


@pytest.mark.parametrize(("password", "code"), [("abcd", "password_length"), ("x" * 257, "password_length"),
                                                (" " * 5, "password_blank"), ("", "password_length")])
def test_password_policy(password, code):
    with pytest.raises(passwords.PasswordPolicyError) as error:
        passwords.check_policy(password)
    assert error.value.code == code


@pytest.mark.parametrize("password", ["abcde", "ana12", "x" * 256, " ab c", "contiene-ana-el-usuario"])
def test_default_minimum_is_5_without_composition_or_username_rules(password):
    passwords.check_policy(password)


def test_the_error_states_the_current_minimum(monkeypatch):
    with pytest.raises(passwords.PasswordPolicyError) as default:
        passwords.check_policy("abcd")
    assert "entre 5 y 256" in str(default.value)
    monkeypatch.setenv(passwords.MIN_LENGTH_VARIABLE, "12")
    with pytest.raises(passwords.PasswordPolicyError) as raised:
        passwords.check_policy("abcdefghijk")
    assert "entre 12 y 256" in str(raised.value)
    passwords.check_policy("abcdefghijkl")


@pytest.mark.parametrize(("value", "expected"), [(None, 5), ("", 5), ("  ", 5), ("5", 5), (" 14 ", 14),
                                                 ("256", 256)])
def test_minimum_setting(value, expected):
    assert passwords.min_length(value) == expected


@pytest.mark.parametrize("value", ["4", "0", "257", "-5", "5.5", "abc", "1e2", "\u0661\u0662"])
def test_invalid_minimum_setting_is_reported_clearly(value):
    with pytest.raises(passwords.PasswordConfigError) as error:
        passwords.min_length(value)
    assert "ITGLASSES_PASSWORD_MIN_LENGTH" in str(error.value) and "entre 5 y 256" in str(error.value)


def test_backend_refuses_to_start_with_an_invalid_minimum(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    from backend.app.providers.mock import MockLLM
    monkeypatch.setenv(passwords.MIN_LENGTH_VARIABLE, "3")
    with pytest.raises(passwords.PasswordConfigError):
        with TestClient(create_app(MockLLM())):
            pass
    monkeypatch.setenv(passwords.MIN_LENGTH_VARIABLE, "8")
    with TestClient(create_app(MockLLM())) as client:
        assert client.get("/health").status_code == 200


# --- accounts ---------------------------------------------------------------------------------------------

def test_create_user_normalizes_and_keeps_no_plain_password(path):
    with accounts.connect(path) as db:
        user_id = accounts.create_user(db, "  Ana.Lopez ", GOOD, "user")
        row = accounts.get(db, user_id)
        assert (row["username"], row["display_name"], row["role"], row["status"]) == (
            "ana.lopez", "ana.lopez", "user", "active")
        assert "pw_hash" not in row.keys()  # public fields only
        assert accounts.find(db, "ANA.LOPEZ")["id"] == user_id
    assert not secrets_in(path, GOOD)


@pytest.mark.parametrize("username", ["ab", "-ana", "ana lopez", "ana@x", "a" * 65, ""])
def test_invalid_usernames(path, username):
    with accounts.connect(path) as db, pytest.raises(accounts.AccountError) as error:
        accounts.create_user(db, username, GOOD, "user")
    assert error.value.code == "username_invalid"


def test_duplicate_username_any_case_invalid_role_and_display_name(path):
    with accounts.connect(path) as db:
        accounts.create_user(db, "ana", GOOD, "user")
        with pytest.raises(accounts.AccountError) as duplicate:
            accounts.create_user(db, "ANA", OTHER, "admin")
        with pytest.raises(accounts.AccountError) as role:
            accounts.create_user(db, "beto", GOOD, "owner")
        with pytest.raises(accounts.AccountError) as name:
            accounts.create_user(db, "carla", GOOD, "user", display_name="Carla‮")
    assert (duplicate.value.code, role.value.code, name.value.code) == (
        "username_taken", "role_invalid", "display_name_invalid")


def test_set_password_closes_sessions_and_clears_the_lockout(path):
    with accounts.connect(path) as db:
        user_id = accounts.create_user(db, "ana", GOOD, "user")
        add_session(db, user_id)
        db.execute("UPDATE users SET failed_count=5, locked_until=9999999999 WHERE id=?", (user_id,))
        accounts.set_password(db, user_id, OTHER, must_change=True)
        row = db.execute("SELECT pw_salt,pw_hash,pw_params,must_change_pw,failed_count,locked_until FROM users "
                         "WHERE id=?", (user_id,)).fetchone()
        assert passwords.verify_password(OTHER, row["pw_salt"], row["pw_hash"], row["pw_params"])
        assert (row["must_change_pw"], row["failed_count"], row["locked_until"]) == (1, 0, None)
        assert open_sessions(db, user_id) == 0


def test_the_last_active_superadmin_cannot_be_disabled(path):
    with accounts.connect(path) as db:
        first = accounts.create_user(db, "root", GOOD, "superadmin")
        with pytest.raises(accounts.AccountError) as error:
            accounts.set_status(db, first, "disabled")
        assert error.value.code == "last_superadmin"
        second = accounts.create_user(db, "root2", OTHER, "superadmin")
        add_session(db, first)
        accounts.set_status(db, first, "disabled")
        assert accounts.get(db, first)["status"] == "disabled" and open_sessions(db, first) == 0
        with pytest.raises(accounts.AccountError):
            accounts.set_status(db, second, "disabled")


def test_only_owned_devices_without_owner_are_assigned(path):
    for device_id, state in (("a", "active"), ("b", "approved"), ("c", "blocked"), ("d", "pending"),
                             ("e", "denied")):
        add_device(path, device_id, state)
    add_device(path, "f", "active", owner="someone-else")
    with accounts.connect(path) as db:
        user_id = accounts.create_user(db, "root", GOOD, "superadmin")
        assert accounts.assign_unowned_devices(db, user_id, assigned_by=user_id) == 3
        owners = dict(db.execute("SELECT id,user_id FROM installs").fetchall())
    assert owners == {"a": user_id, "b": user_id, "c": user_id, "d": None, "e": None, "f": "someone-else"}


# --- audit ------------------------------------------------------------------------------------------------

def test_audit_records_and_reads_back(path):
    with accounts.connect(path) as db:
        entry = audit.record(db, "user.create", actor_kind="script", target_type="user", target_id="u1",
                             detail={"username": "ana", "role": "user"}, ip="127.0.0.1", now=100)
        [row] = audit.entries(db)
    assert row["id"] == entry and row["at"] == 100 and row["detail"] == {"username": "ana", "role": "user"}


@pytest.mark.parametrize("detail", [{"password": "x"}, {"new_token": "x"}, {"pw_hash": "x"}, {"csrf": "x"},
                                    {"nested": {"a": 1}}, {"list": [1]}, {"text": "x" * 3000}, ["not", "dict"]])
def test_audit_refuses_secrets_and_odd_details(path, detail):
    with accounts.connect(path) as db, pytest.raises(ValueError):
        audit.record(db, "user.create", actor_kind="script", detail=detail)


def test_audit_refuses_unknown_actions_and_kinds(path):
    with accounts.connect(path) as db:
        with pytest.raises(ValueError):
            audit.record(db, "user.teleport", actor_kind="script")
        with pytest.raises(ValueError):
            audit.record(db, "user.create", actor_kind="robot")
        with pytest.raises(ValueError):
            audit.record(db, "user.create", actor_kind="user", outcome="maybe")


# --- local scripts ----------------------------------------------------------------------------------------

def answers(*values):
    queue = list(values)
    return lambda _prompt="": queue.pop(0)


def run(path, *argv, ask=None, read=None):
    return cli.main([*argv, "--db", str(path)], ask=ask or answers(), read=read or answers())


def test_create_superadmin_assigns_devices_and_is_audited(path, capsys):
    add_device(path, "phone-1", "active")
    add_device(path, "phone-2", "pending")
    assert run(path, "create-superadmin", ask=answers("Enrique", "Enrique E."), read=answers(GOOD, GOOD)) == 0
    out = capsys.readouterr().out
    assert "Superadmin 'enrique' creado" in out and ": 1." in out and GOOD not in out
    with accounts.connect(path) as db:
        row = accounts.find(db, "enrique")
        assert (row["role"], row["display_name"]) == ("superadmin", "Enrique E.")
        assert dict(db.execute("SELECT id,user_id FROM installs").fetchall()) == {"phone-1": row["id"],
                                                                                 "phone-2": None}
        [entry] = audit.entries(db)
    assert (entry["action"], entry["actor_kind"], entry["detail"]) == (
        "bootstrap.superadmin", "script", {"username": "enrique", "devices_assigned": 1})
    assert not secrets_in(path, GOOD)


def test_a_second_superadmin_is_refused(path, capsys):
    assert run(path, "create-superadmin", "--username", "root", "--name", "", read=answers(GOOD, GOOD)) == 0
    assert run(path, "create-superadmin", "--username", "otro", read=answers(OTHER, OTHER)) == 2
    with accounts.connect(path) as db:
        assert [u["username"] for u in accounts.list_users(db)] == ["root"]
    assert "Ya existe un superadmin" in capsys.readouterr().out


@pytest.mark.parametrize("typed", [(GOOD, OTHER), ("cort", "cort"), ("     ", "     ")])
def test_create_superadmin_with_a_bad_password_changes_nothing(path, typed, capsys):
    assert run(path, "create-superadmin", "--username", "root", "--name", "", read=answers(*typed)) == 1
    with accounts.connect(path) as db:
        assert accounts.list_users(db) == [] and audit.entries(db) == []
    assert "No se hizo ningun cambio" in capsys.readouterr().out


def test_reset_password_unlock_and_enable(path, capsys):
    run(path, "create-superadmin", "--username", "root", "--name", "", read=answers(GOOD, GOOD))
    with accounts.connect(path) as db:
        user_id = accounts.find(db, "root")["id"]
        add_session(db, user_id)
        db.execute("UPDATE users SET failed_count=9, locked_until=9999999999 WHERE id=?", (user_id,))
    assert run(path, "reset-password", "--username", "ROOT", read=answers(OTHER, OTHER)) == 0
    with accounts.connect(path) as db:
        secret = db.execute("SELECT pw_salt,pw_hash,pw_params,locked_until FROM users WHERE id=?",
                            (user_id,)).fetchone()
        assert passwords.verify_password(OTHER, secret["pw_salt"], secret["pw_hash"], secret["pw_params"])
        assert secret["locked_until"] is None and open_sessions(db, user_id) == 0
        db.execute("UPDATE users SET locked_until=9999999999, status='disabled' WHERE id=?", (user_id,))
    assert run(path, "unlock", "--username", "root") == 0
    assert run(path, "enable", "--username", "root") == 0
    with accounts.connect(path) as db:
        row = accounts.get(db, user_id)
        assert (row["status"], row["locked_until"]) == ("active", None)
        actions = [e["action"] for e in audit.entries(db)]
    assert actions == ["user.enable", "user.unlock", "user.password_reset", "bootstrap.superadmin"]
    assert OTHER not in capsys.readouterr().out and not secrets_in(path, GOOD, OTHER)


def test_temporary_reset_forces_a_change_at_next_login(path):
    run(path, "create-superadmin", "--username", "root", "--name", "", read=answers(GOOD, GOOD))
    assert run(path, "reset-password", "--username", "root", "--temporary", read=answers(OTHER, OTHER)) == 0
    with accounts.connect(path) as db:
        assert accounts.find(db, "root")["must_change_pw"] == 1


def test_unknown_user_and_list(path, capsys):
    assert run(path, "reset-password", "--username", "nadie") == 3
    assert run(path, "list") == 0
    assert "No hay usuarios" in capsys.readouterr().out
    run(path, "create-superadmin", "--username", "root", "--name", "", read=answers(GOOD, GOOD))
    assert run(path, "list") == 0
    out = capsys.readouterr().out
    assert "root" in out and "superadmin" in out and "active" in out


def test_ctrl_c_cancels_without_changes(path, capsys):
    def interrupted(_prompt=""):
        raise KeyboardInterrupt
    assert run(path, "create-superadmin", "--username", "root", "--name", "", read=interrupted) == 1
    with accounts.connect(path) as db:
        assert accounts.list_users(db) == []


def test_scripts_apply_the_configured_minimum_and_say_it(path, monkeypatch, capsys):
    monkeypatch.setenv(passwords.MIN_LENGTH_VARIABLE, "8")
    prompts = []

    def read(prompt=""):
        prompts.append(prompt)
        return "seis66"

    assert run(path, "create-superadmin", "--username", "root", "--name", "", read=read) == 1
    assert "minimo 8 caracteres" in prompts[0]
    assert "entre 8 y 256" in capsys.readouterr().out
    assert run(path, "create-superadmin", "--username", "root", "--name", "", read=answers("ocho8888", "ocho8888")) == 0
    assert run(path, "reset-password", "--username", "root", read=answers("siete77", "siete77")) == 1
    assert run(path, "reset-password", "--username", "root", read=answers("nueve9999", "nueve9999")) == 0


def test_scripts_report_an_invalid_minimum_and_change_nothing(path, monkeypatch, capsys):
    monkeypatch.setenv(passwords.MIN_LENGTH_VARIABLE, "cuatro")
    assert run(path, "create-superadmin", "--username", "root", "--name", "", read=answers(GOOD, GOOD)) == 1
    assert "ITGLASSES_PASSWORD_MIN_LENGTH" in capsys.readouterr().out
    with accounts.connect(path) as db:
        assert accounts.list_users(db) == []


def test_accounts_apply_the_same_minimum(path, monkeypatch):
    with accounts.connect(path) as db:
        user_id = accounts.create_user(db, "ana", "cinco", "user")  # 5 characters: the default minimum
        monkeypatch.setenv(passwords.MIN_LENGTH_VARIABLE, "10")
        with pytest.raises(passwords.PasswordPolicyError):
            accounts.create_user(db, "beto", "nueve9999", "user")
        with pytest.raises(passwords.PasswordPolicyError):
            accounts.set_password(db, user_id, "nueve9999")
        accounts.set_password(db, user_id, "diez-10-10")
