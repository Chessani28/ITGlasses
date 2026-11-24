"""Diagnostico aislado de la busqueda publica: cada buscador de ddgs con y sin site:, y WebSearchService con sus
intentos y diagnosticos (docs/history.md, busqueda publica).

Uso, desde D:\\ITGlasses:
    .venv\\Scripts\\python scripts\\diag_web_search.py

Solo envia consultas publicas fijas sobre Windows 11. No lee secretos ni la base de conocimiento.
"""
import asyncio
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ddgs  # noqa: E402
from ddgs import DDGS  # noqa: E402

QUERIES = [
    "Windows 11 latest version release information site:learn.microsoft.com",  # la que arma el backend
    "Windows 11 latest version release information",                           # sin site:
]
BACKENDS = ["auto", "duckduckgo", "bing", "brave", "google", "mojeek", "startpage", "yahoo", "yandex", "wikipedia"]


def raw(query, backend):
    started = time.monotonic()
    try:
        with DDGS(timeout=15) as client:
            items = list(client.text(query, max_results=5, backend=backend))
        hosts = [urlsplit(item.get("href") or "").hostname for item in items]
        ms = (time.monotonic() - started) * 1000
        official = sum(1 for host in hosts if host and host.endswith(".microsoft.com"))
        return f"{len(items)} resultados, {official} de *.microsoft.com, {ms:.0f} ms: {hosts}"
    except Exception as exc:  # noqa: BLE001 - es un diagnostico
        return f"ERROR {type(exc).__name__}: {str(exc)[:160]}"


async def service():
    from backend.app.assistant.web_search import WebSearchService
    web = WebSearchService()
    results = await web.search("¿Cuál es la última versión de Windows 11?", max_results=5)
    print("  last_error:", repr(web.last_error))
    print("  diagnostics:", web.last_diagnostics)
    for result in results:
        print("  -", result.url, "| verified:", getattr(result, "verified", None), "|", (result.snippet or "")[:100])


if __name__ == "__main__":
    print("ddgs", getattr(ddgs, "__version__", "?"))
    for query in QUERIES:
        print("\nConsulta:", query)
        for backend in BACKENDS:
            print(f"  {backend:<11}", raw(query, backend))
    print("\nWebSearchService (lo mismo que usa el asistente):")
    asyncio.run(service())
