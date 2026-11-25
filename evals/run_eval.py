"""Corre el examen del cerebro en proceso (sin servidor ni puerto 8000) y escribe el reporte.

    python -m evals.run_eval [--provider ollama|mock] [--category X] [--case ID] [--compare RUTA] [--cases-dir DIR]

Con ollama usa el modelo real y una COPIA del índice de manuales en .cache/eval/; nunca escribe en storage/.
"""
import argparse
import asyncio
import inspect
import json
import shutil
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

import httpx

from backend.app.core.config import PROJECT_ROOT
from evals.cases import load_cases
from evals.checks import check_turn
from evals.report import compare, summarize, to_markdown

EVALS_DIR = Path(__file__).resolve().parent
CASES_DIR = EVALS_DIR / "cases"
RESULTS_DIR = EVALS_DIR / "results"
WORK_ROOT = PROJECT_ROOT / ".cache" / "eval"
HEALTH_URL = "http://127.0.0.1:8000/health"


def backend_live():
    """True si el backend real responde en el puerto 8000 (solo se consulta, nunca se usa)."""
    try:
        return httpx.get(HEALTH_URL, timeout=1.0, trust_env=False).status_code == 200
    except httpx.HTTPError:
        return False


def _copy_index(work_dir):
    source = PROJECT_ROOT / "storage" / "knowledge" / "knowledge.sqlite3"
    target = work_dir / "storage" / "knowledge" / "knowledge.sqlite3"
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.exists():
        # La API de respaldo de sqlite lee también lo pendiente en el WAL y no escribe en el original.
        with closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as src, \
                closing(sqlite3.connect(target)) as dst:
            src.backup(dst)


def _document_texts(knowledge):
    texts = {}
    for row in knowledge.store.eligible_chunks():
        texts.setdefault(row["title"], []).append(row["text"])
    return {title: "\n".join(parts) for title, parts in texts.items()}


class OfflineWebSearch:
    """Búsqueda pública vacía para --provider mock: el examen de prueba nunca sale a internet."""

    last_error = None

    async def search(self, query, max_results=5):
        return []


def build_assistant(provider_name, work_dir):
    """El asistente como lo arma el backend, contra una copia del índice; y título → texto de cada manual."""
    from backend.app.assistant.service import UnifiedAssistantService
    work_dir = Path(work_dir)
    if provider_name == "mock":
        from backend.app.providers.mock import MockLLM
        return UnifiedAssistantService(None, MockLLM(), web_search=OfflineWebSearch()), {}
    from backend.app.assistant.web_search import WebSearchService
    from backend.app.core.config import settings
    from backend.app.knowledge.embeddings import OllamaEmbeddings
    from backend.app.knowledge.grounding import EvidenceSelector
    from backend.app.knowledge.service import KnowledgeService
    from backend.app.providers.ollama import OllamaProvider
    _copy_index(work_dir)
    knowledge = KnowledgeService(work_dir, OllamaEmbeddings(settings.ollama_url),
                                 EvidenceSelector(settings.model, settings.ollama_url, settings.keep_alive))
    assistant = UnifiedAssistantService(knowledge, OllamaProvider(settings), web_search=WebSearchService())
    return assistant, _document_texts(knowledge)


def _accepts_channel(assistant):
    try:
        return "channel" in inspect.signature(assistant.chat).parameters
    except (TypeError, ValueError):
        return False


async def run_case(assistant, case, document_texts):
    """Corre los turnos de un caso en una sesión nueva y revisa cada uno."""
    from backend.app.assistant.speech import spoken_summary
    session_id, turns = None, []
    with_channel = _accepts_channel(assistant)
    for turn in case.turns:
        started = time.perf_counter()
        try:
            if with_channel:
                result = await assistant.chat(turn.message, session_id, channel=case.channel)
            else:
                result = await assistant.chat(turn.message, session_id)
            error = None
        except Exception as exc:  # noqa: BLE001 - un turno roto es un fallo del caso, no del examen
            result, error = {}, f"error: {type(exc).__name__}"
        latency = round((time.perf_counter() - started) * 1000)
        result = dict(result)
        # Igual que la ruta HTTP: el speech del asistente si lo trae, si no el resumen de siempre.
        speech = result.pop("speech") if "speech" in result else spoken_summary(result.get("answer"))
        result["speech"] = speech
        session_id = result.get("session_id") or session_id
        failures = [error] if error else (check_turn(turn.expect, result, document_texts) if turn.expect else [])
        turns.append({"message": turn.message, "answer": result.get("answer"), "speech": speech,
                      "route": result.get("route"), "latency_ms": latency, "failures": failures})
        if error:
            break
    return {"id": case.id, "category": case.category, "channel": case.channel,
            "passed": all(not turn["failures"] for turn in turns), "turns": turns}


def _latest_report():
    reports = sorted(RESULTS_DIR.glob("*.json")) if RESULTS_DIR.exists() else []
    return reports[-1] if reports else None


def _parse(argv):
    parser = argparse.ArgumentParser(prog="python -m evals.run_eval", description="Examen del cerebro de ITGlasses")
    parser.add_argument("--provider", choices=("ollama", "mock"), default="ollama")
    parser.add_argument("--category", action="append")
    parser.add_argument("--case", action="append")
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--cases-dir", type=Path, action="append")
    return parser.parse_args(argv)


async def _run_all(assistant, cases, document_texts):
    results = []
    for index, case in enumerate(cases, start=1):
        result = await run_case(assistant, case, document_texts)
        print(f"[{index}/{len(cases)}] {'OK  ' if result['passed'] else 'FALLA'} {case.id}", flush=True)
        results.append(result)
    return results


def main(argv=None):
    args = _parse(argv)
    directories = args.cases_dir or [CASES_DIR, *([CASES_DIR / "local"] if (CASES_DIR / "local").exists() else [])]
    cases, load_errors = load_cases(directories)
    if args.category:
        cases = [case for case in cases if case.category in args.category]
    if args.case:
        cases = [case for case in cases if case.id in args.case]
    live = backend_live()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    work_dir = Path(WORK_ROOT) / stamp
    previous_path = args.compare or _latest_report()
    try:
        assistant, document_texts = build_assistant(args.provider, work_dir)
        results = asyncio.run(_run_all(assistant, cases, document_texts))
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    summary = summarize(results, load_errors, live)
    previous = None
    if previous_path and Path(previous_path).exists():
        previous = json.loads(Path(previous_path).read_text(encoding="utf-8")).get("summary")
    comparison = compare(summary, previous)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    report = {"created": stamp, "provider": args.provider,
              "compared_with": str(previous_path) if previous else None,
              "summary": summary, "comparison": comparison, "results": results}
    (RESULTS_DIR / f"{stamp}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown = to_markdown(summary, comparison)
    (RESULTS_DIR / f"{stamp}.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"Reporte: {RESULTS_DIR / (stamp + '.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
