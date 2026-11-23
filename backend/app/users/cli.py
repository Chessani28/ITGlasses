r"""Local administration of web accounts, on this PC only (never from the web).

    .venv\Scripts\python -m backend.app.users.cli create-superadmin
    .venv\Scripts\python -m backend.app.users.cli reset-password --username NAME [--temporary]
    .venv\Scripts\python -m backend.app.users.cli unlock --username NAME
    .venv\Scripts\python -m backend.app.users.cli enable --username NAME
    .venv\Scripts\python -m backend.app.users.cli list

Wrappers: scripts\create-superadmin.ps1 and scripts\reset-access.ps1. Passwords are read without echo (getpass)
and are never printed or logged. Every change goes to the audit log as actor "script". The backend may keep
running: each command is one SQLite transaction.
"""
import argparse
import getpass
import sys
import time

from . import accounts, audit, passwords


def _password(read, username):
    first = read(f"Nueva contrasena para '{username}' (minimo {passwords.min_length()} caracteres): ")
    if first != read("Repite la contrasena: "):
        raise accounts.AccountError("password_mismatch", "Las contrasenas no coinciden.")
    passwords.check_policy(first)
    return first


def _username(args, ask):
    return accounts.normalize_username(args.username if args.username else ask("Usuario: "))


def create_superadmin(args, ask, read):
    with accounts.connect(args.db) as db:
        if accounts.any_superadmin(db):
            print("Ya existe un superadmin. Para recuperar el acceso usa reset-access "
                  "(reset-password, unlock o enable).")
            return 2
    username = _username(args, ask)
    display = args.name if args.name is not None else ask(f"Nombre a mostrar [{username}]: ")
    password = _password(read, username)
    with accounts.connect(args.db) as db:
        if accounts.any_superadmin(db):  # someone else created one meanwhile
            print("Ya existe un superadmin; no se creo otro.")
            return 2
        user_id = accounts.create_user(db, username, password, "superadmin", display or None)
        assigned = accounts.assign_unowned_devices(db, user_id, assigned_by=user_id)
        audit.record(db, "bootstrap.superadmin", actor_kind="script", target_type="user", target_id=user_id,
                     detail={"username": username, "devices_assigned": assigned})
    print(f"Superadmin '{username}' creado. Dispositivos ya aprobados asignados a este usuario: {assigned}.")
    return 0


def _existing(args, ask):
    username = _username(args, ask)
    with accounts.connect(args.db) as db:
        row = accounts.find(db, username)
    if row is None:
        print("No existe ese usuario.")
    return username, row


def reset_password(args, ask, read):
    username, row = _existing(args, ask)
    if row is None:
        return 3
    password = _password(read, username)
    with accounts.connect(args.db) as db:
        row = accounts.find(db, username)
        if row is None:
            print("No existe ese usuario.")
            return 3
        accounts.set_password(db, row["id"], password, must_change=args.temporary)
        audit.record(db, "user.password_reset", actor_kind="script", target_type="user", target_id=row["id"],
                     detail={"username": username, "temporary": bool(args.temporary)})
    extra = " Debera cambiarla al entrar." if args.temporary else ""
    print(f"Contrasena de '{username}' restablecida; sus sesiones se cerraron y se quito el bloqueo.{extra}")
    return 0


def unlock(args, ask, read):
    username, row = _existing(args, ask)
    if row is None:
        return 3
    with accounts.connect(args.db) as db:
        accounts.unlock(db, row["id"])
        audit.record(db, "user.unlock", actor_kind="script", target_type="user", target_id=row["id"],
                     detail={"username": username})
    print(f"'{username}' desbloqueado.")
    return 0


def enable(args, ask, read):
    username, row = _existing(args, ask)
    if row is None:
        return 3
    with accounts.connect(args.db) as db:
        accounts.set_status(db, row["id"], "active")
        accounts.unlock(db, row["id"])
        audit.record(db, "user.enable", actor_kind="script", target_type="user", target_id=row["id"],
                     detail={"username": username})
    print(f"'{username}' activado y desbloqueado.")
    return 0


def _when(value):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(value)) if value else "-"


def list_accounts(args, ask, read):
    with accounts.connect(args.db) as db:
        users = accounts.list_users(db)
    if not users:
        print("No hay usuarios. Crea el primero con scripts\\create-superadmin.ps1")
        return 0
    now = time.time()
    print(f"{'usuario':<24} {'rol':<11} {'estado':<9} {'bloqueado hasta':<17} ultimo acceso")
    for user in users:
        locked = _when(user["locked_until"]) if user["locked_until"] and user["locked_until"] > now else "-"
        print(f"{user['username']:<24} {user['role']:<11} {user['status']:<9} {locked:<17} "
              f"{_when(user['last_login_at'])}")
    return 0


COMMANDS = {"create-superadmin": create_superadmin, "reset-password": reset_password, "unlock": unlock,
            "enable": enable, "list": list_accounts}


def main(argv=None, ask=input, read=getpass.getpass):
    parser = argparse.ArgumentParser(prog="python -m backend.app.users.cli",
                                     description="Administracion local de cuentas web de ITGlasses.")
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--username")
    parser.add_argument("--name", help="nombre a mostrar (create-superadmin)")
    parser.add_argument("--temporary", action="store_true",
                        help="reset-password: obliga a cambiar la contrasena al entrar")
    parser.add_argument("--db", help="base de dispositivos (por defecto la configurada)")
    args = parser.parse_args(argv)
    try:
        return COMMANDS[args.command](args, ask, read)
    except (accounts.AccountError, passwords.PasswordPolicyError, passwords.PasswordConfigError) as error:
        print(f"No se hizo ningun cambio: {error}")
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nCancelado; no se hizo ningun cambio.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
