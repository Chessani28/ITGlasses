"""Prueba de carga: muchos técnicos preguntando a la vez contra el modelo real, en una copia aislada.

    python -m evals.load_test [--levels 1,3,5,10,20] [--turns 3] [--port 8010]

Arranca un backend aparte (evals.load_server) en el puerto 8010 con su propia carpeta en .cache/load/<hora>/:
usuarios, historial y dispositivos de prueba, y una COPIA del índice de manuales. Nunca escribe en storage/ ni usa
el puerto 8000 ni Caddy. Cada técnico entra con su usuario, dice su número de técnico, hace preguntas generales y
de manuales y al final pregunta su número: así se ve si una conversación se mezcla con otra. El reporte (JSON y
Markdown) queda en esa carpeta; las contraseñas de prueba solo viven en memoria.
"""
import argparse
import asyncio
import json
import os
import re
import secrets
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

import httpx

from backend.app.core.config import PROJECT_ROOT

WORK_ROOT = PROJECT_ROOT / ".cache" / "load"
GENERAL = ["¿Qué es el DNS y para qué sirve?", "¿Cómo libero espacio en el disco de una PC con Windows?",
           "¿Qué diferencia hay entre un switch y un router?", "¿Qué hago si una impresora de red no imprime?",
           "¿Qué es una dirección IP fija?", "¿Cómo sé si un correo es phishing?",
           "¿Para qué sirve una VLAN?", "¿Qué revisar si una PC no prende?"]
FOLLOW_UP = ["Explícalo más corto", "¿Y cuál es el primer paso?", "Dame un ejemplo"]
MANUAL = "Según el manual {title}, ¿cuáles son los primeros pasos?"


# --- what a technician does --------------------------------------------------------------------------------------

def badge(index):
    """Número de técnico único por usuario: si aparece en la respuesta de otro, las conversaciones se mezclaron."""
    return str(4000 + index * 37)


def script(index, turns, titles):
    """Los mensajes de un técnico: su número, `turns` preguntas (general, seguimiento, manual) y su número otra vez."""
    questions = []
    for step in range(turns):
        kind = step % 3
        if kind == 2 and titles:
            questions.append(MANUAL.format(title=titles[(index + step) % len(titles)]))
        elif kind == 1:
            questions.append(FOLLOW_UP[(index + step) % len(FOLLOW_UP)])
        else:
            questions.append(GENERAL[(index + step) % len(GENERAL)])
    return ([f"Hola, mi número de técnico es {badge(index)}. Solo recuérdalo, contesta breve."]
            + questions + ["¿Cuál es mi número de técnico? Contesta solo el número."])


def mixed_with(answer, own, everyone):
    """Los números de OTROS técnicos que aparecen en la respuesta (debe quedar vacío)."""
    found = set(re.findall(r"\d{4}", answer or ""))
    return sorted((found & set(everyone)) - {own})


class Technician:
    def __init__(self, client, base, username, password, index, everyone):
        self.client, self.base, self.username, self.password = client, base, username, password
        self.index, self.everyone = index, everyone
        self.headers = {"Origin": base}
        self.turns = []

    async def login(self):
        response = await self.client.post(f"{self.base}/api/v1/auth/login", headers=self.headers,
                                          json={"username": self.username, "password": self.password})
        response.raise_for_status()
        token = response.cookies.get("itg_session")
        # La cookie es Secure (la web real va por HTTPS en la LAN); aquí se manda a mano por HTTP local.
        self.headers = {"Origin": self.base, "Cookie": f"itg_session={token}",
                        "X-CSRF-Token": response.json()["csrf_token"]}

    async def ask(self, message, session_id):
        started = time.perf_counter()
        try:
            response = await self.client.post(f"{self.base}/api/v1/assistant/chat", headers=self.headers,
                                              json={"message": message, "session_id": session_id})
            status = response.status_code
            data = response.json() if "json" in response.headers.get("content-type", "") else {}
        except httpx.TimeoutException:
            status, data = "timeout", {}
        except httpx.HTTPError as error:
            status, data = type(error).__name__, {}
        ms = round((time.perf_counter() - started) * 1000)
        error = None if status == 200 else (data.get("error", {}).get("code") if isinstance(data.get("error"), dict)
                                             else data.get("detail") or status)
        self.turns.append({"message": message, "status": status, "error": error, "ms": ms,
                           "route": data.get("route"), "answer": (data.get("answer") or "")[:400],
                           "session_id": data.get("session_id")})
        return data.get("session_id") or session_id

    async def run(self, messages):
        session_id = None
        for message in messages:
            session_id = await self.ask(message, session_id)
        last = self.turns[-1]
        own = badge(self.index)
        return {"user": self.username, "turns": self.turns,
                "remembers": last["status"] == 200 and own in (last["answer"] or ""),
                "mixed": sorted({n for turn in self.turns for n in mixed_with(turn["answer"], own, self.everyone)})}

    async def own_conversations(self):
        response = await self.client.get(f"{self.base}/api/v1/conversations", headers=self.headers)
        return response.json().get("conversations", []) if response.status_code == 200 else None


# --- one level and its summary -----------------------------------------------------------------------------------

def percentile(values, share):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(share * (len(ordered) - 1))))] if ordered else None


def summarize(level, results, seconds):
    turns = [turn for result in results for turn in result["turns"]]
    good = [turn["ms"] for turn in turns if turn["status"] == 200]
    errors = {}
    for turn in turns:
        if turn["status"] != 200:
            errors[str(turn["error"])] = errors.get(str(turn["error"]), 0) + 1
    return {"users": level, "questions": len(turns), "ok": len(good), "errors": errors,
            "p50_s": round(statistics.median(good) / 1000, 1) if good else None,
            "p90_s": round(percentile(good, 0.9) / 1000, 1) if good else None,
            "max_s": round(max(good) / 1000, 1) if good else None,
            "remember": sum(result["remembers"] for result in results),
            "mixed": sum(bool(result["mixed"]) for result in results),
            "foreign_history": sum(result.get("foreign_history", 0) for result in results),
            "wall_s": round(seconds, 1)}


async def run_level(client, base, accounts, level, turns, titles, owned):
    """`owned`: usuario → conversaciones que ese técnico abrió (en todos los niveles); se va llenando."""
    everyone = [badge(index) for index in range(len(accounts))]
    people = [Technician(client, base, name, password, index, everyone)
              for index, (name, password) in enumerate(accounts[:level])]
    for person in people:
        await person.login()
    started = time.perf_counter()
    results = await asyncio.gather(*(person.run(script(person.index, turns, titles)) for person in people))
    seconds = time.perf_counter() - started
    # Historial: cada técnico ve solo conversaciones que él abrió.
    for person, result in zip(people, results):
        mine = owned.setdefault(person.username, set())
        mine.update(turn["session_id"] for turn in person.turns if turn.get("session_id"))
        listed = await person.own_conversations()
        result["foreign_history"] = (0 if listed is None else
                                     sum(1 for item in listed if item.get("id") not in mine))
    return summarize(level, results, seconds), results


# --- the isolated server -----------------------------------------------------------------------------------------

def copy_index(work):
    source = PROJECT_ROOT / "storage" / "knowledge" / "knowledge.sqlite3"
    target = work / "storage" / "knowledge" / "knowledge.sqlite3"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        return []
    with closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as src, \
            closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
    with closing(sqlite3.connect(target)) as db:
        return [row[0] for row in db.execute(
            "SELECT title FROM documents WHERE authorized=1 AND withdrawn=0 ORDER BY title")]


def server_environment(work, port):
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("ITGLASSES_")}  # nada del backend real (tokens, origen de Caddy)
    temporary = PROJECT_ROOT / ".cache" / "tmp"
    environment.update({
        "ITGLASSES_LOAD_WORK": str(work), "ITGLASSES_PORT": str(port), "ITGLASSES_PROVIDER": "ollama",
        "ITGLASSES_PAIRING_DB": str(work / "pairing" / "devices.sqlite3"),
        "ITGLASSES_HISTORY_DB": str(work / "history" / "history.sqlite3"),
        "ITGLASSES_BACKUP_DIR": str(work / "backups"), "ITGLASSES_DAILY_BACKUP": "0",
        "ITGLASSES_PIPER_PERSISTENT": "0", "ITGLASSES_WHISPER_PRELOAD": "0",
        "TEMP": str(temporary), "TMP": str(temporary)})
    return environment


def create_accounts(environment, count):
    """Usuarios de prueba en la base de la copia (contraseñas aleatorias, solo en memoria)."""
    from backend.app.users import accounts
    path = Path(environment["ITGLASSES_PAIRING_DB"])
    path.parent.mkdir(parents=True, exist_ok=True)
    made = []
    with accounts.connect(path) as db:
        accounts.create_user(db, "carga-admin", secrets.token_urlsafe(18), "superadmin")
        for index in range(count):
            name, password = f"tecnico{index + 1:02d}", secrets.token_urlsafe(18)
            accounts.create_user(db, name, password, "user")
            made.append((name, password))
    return made


def port_free(port):
    try:
        httpx.get(f"http://127.0.0.1:{port}/health", timeout=1.0, trust_env=False)
        return False
    except httpx.HTTPError:
        return True


async def wait_healthy(base, process, seconds=120):
    deadline = time.monotonic() + seconds
    async with httpx.AsyncClient(trust_env=False) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("el backend de prueba se cerró al arrancar")
            try:
                if (await client.get(f"{base}/health", timeout=2.0)).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
    raise RuntimeError("el backend de prueba no respondió")


# --- report ------------------------------------------------------------------------------------------------------

def to_markdown(summaries, config):
    lines = [f"# Prueba de carga — {config['started']}", "",
             f"Modelo {config['model']}, {config['turns']} preguntas por técnico más 2 de memoria, "
             f"{config['manuals']} manuales en la copia.", "",
             "| Técnicos | Preguntas | Bien | Errores | Mediana | p90 | Máx. | Recuerda su número | Mezclas | Duración |",
             "|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|"]
    for row in summaries:
        errors = ", ".join(f"{code}×{count}" for code, count in row["errors"].items()) or "—"
        lines.append(f"| {row['users']} | {row['questions']} | {row['ok']} | {errors} | {row['p50_s']} s | "
                     f"{row['p90_s']} s | {row['max_s']} s | {row['remember']}/{row['users']} | "
                     f"{row['mixed'] + row['foreign_history']} | {row['wall_s']} s |")
    return "\n".join(lines) + "\n"


async def main_async(args):
    port = args.port
    if port == 8000:
        sys.exit("La prueba de carga nunca usa el puerto 8000.")
    if not port_free(port):
        sys.exit(f"El puerto {port} ya está ocupado.")
    started = datetime.now().strftime("%Y%m%d-%H%M%S")
    work = WORK_ROOT / started
    work.mkdir(parents=True)
    titles = copy_index(work)
    environment = server_environment(work, port)
    levels = [int(value) for value in args.levels.split(",")]
    accounts = create_accounts(environment, max(levels))
    base = f"http://127.0.0.1:{port}"
    log = open(work / "server.log", "w", encoding="utf-8")
    process = subprocess.Popen([sys.executable, "-m", "evals.load_server"], cwd=PROJECT_ROOT, env=environment,
                               stdout=log, stderr=subprocess.STDOUT)
    from backend.app.core.config import settings
    config = {"started": started, "model": settings.model, "turns": args.turns, "manuals": len(titles),
              "levels": levels}
    summaries, details = [], {}
    try:
        await wait_healthy(base, process)
        timeout = httpx.Timeout(args.timeout, connect=5.0)
        limits = httpx.Limits(max_connections=200, max_keepalive_connections=50)
        async with httpx.AsyncClient(trust_env=False, timeout=timeout, limits=limits) as client:
            # Una pregunta sola primero: el modelo ya cargado no cuenta como espera de los técnicos.
            owned = {}
            await run_level(client, base, accounts[:1], 1, 0, [], owned)
            for level in levels:
                summary, results = await run_level(client, base, accounts, level, args.turns, titles, owned)
                summaries.append(summary)
                details[str(level)] = results
                print(json.dumps(summary, ensure_ascii=False), flush=True)
                (work / "report.json").write_text(json.dumps({"config": config, "summaries": summaries,
                                                              "details": details}, ensure_ascii=False, indent=1),
                                                  encoding="utf-8")
                (work / "report.md").write_text(to_markdown(summaries, config), encoding="utf-8")
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()
    print(to_markdown(summaries, config))
    print(f"Reporte: {work / 'report.md'}")


def parse(argv):
    parser = argparse.ArgumentParser(prog="python -m evals.load_test", description="Prueba de carga de ITGlasses")
    parser.add_argument("--levels", default="1,3,5,10,20")
    parser.add_argument("--turns", type=int, default=3)
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--timeout", type=float, default=300.0)
    return parser.parse_args(argv)


def main(argv=None):
    asyncio.run(main_async(parse(argv)))


if __name__ == "__main__":
    main()
