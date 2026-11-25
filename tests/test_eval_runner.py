"""Examen del cerebro: corredor y reporte, con asistentes falsos (sin Ollama ni red)."""
import asyncio
import json
import sqlite3
from contextlib import closing

import evals.run_eval as run_eval
from evals.cases import Case, Turn
from evals.report import compare, summarize, to_markdown


class FakeAssistant:
    def __init__(self, answer="Una VPN crea un túnel cifrado.", route="general"):
        self.answer, self.route = answer, route
        self.calls = []

    async def chat(self, message, session_id=None, channel="text"):
        self.calls.append({"message": message, "session_id": session_id, "channel": channel})
        return {"answer": self.answer, "route": self.route, "sources": [], "session_id": session_id or "s-1"}


class OldAssistant:
    """Como el código anterior al modo voz: chat sin channel."""

    async def chat(self, message, session_id=None):
        return {"answer": "hola", "route": "social", "sources": [], "session_id": "s-1"}


def case(channel="text", turns=None):
    return Case("c1", "conceptos", channel,
                turns or [Turn("¿Qué es una VPN?", {"route": "general", "contains_any": ["tunel"]}),
                          Turn("sí", {"route": "general"})], "a.json")


def run(assistant, test_case):
    return asyncio.run(run_eval.run_case(assistant, test_case, {}))


def test_run_case_threads_the_session_and_measures_latency():
    assistant = FakeAssistant()
    result = run(assistant, case())
    assert [call["session_id"] for call in assistant.calls] == [None, "s-1"]
    assert result["passed"] is True
    assert result["id"] == "c1" and result["category"] == "conceptos"
    assert all(turn["latency_ms"] >= 0 and turn["failures"] == [] for turn in result["turns"])


def test_run_case_reports_failures():
    result = run(FakeAssistant(route="knowledge"), case())
    assert result["passed"] is False
    assert "route" in result["turns"][0]["failures"][0]


def test_run_case_passes_the_channel():
    assistant = FakeAssistant()
    run(assistant, case(channel="voice"))
    assert {call["channel"] for call in assistant.calls} == {"voice"}


def test_run_case_works_with_an_assistant_without_channel():
    result = run(OldAssistant(), case(turns=[Turn("hola", {"route": "social"})]))
    assert result["passed"] is True


def test_run_case_uses_the_assistant_speech_when_present():
    class Speaking(FakeAssistant):
        async def chat(self, message, session_id=None, channel="text"):
            return {**await super().chat(message, session_id, channel), "speech": "corto"}
    result = run(Speaking(answer="x" * 900), case(turns=[Turn("a", {"max_speech_chars": 10})]))
    assert result["turns"][0]["speech"] == "corto" and result["passed"] is True


def test_run_case_records_an_exception_as_a_failure():
    class Broken:
        async def chat(self, message, session_id=None, channel="text"):
            raise RuntimeError("boom")
    result = run(Broken(), case())
    assert result["passed"] is False
    assert "RuntimeError" in result["turns"][0]["failures"][0]


def result(case_id, category, passed, latency):
    return {"id": case_id, "category": category, "passed": passed,
            "turns": [{"latency_ms": latency, "failures": [] if passed else ["route: x"]}]}


def test_summarize_groups_by_category_with_median_and_p90():
    results = [result(f"c{i}", "conceptos", i != 0, 100 * (i + 1)) for i in range(10)]
    results.append(result("s1", "social", True, 5))
    summary = summarize(results, ["a.json:x: turns vacío"], backend_live=False)
    assert summary["total"] == {"passed": 10, "cases": 11}
    concepts = summary["categories"]["conceptos"]
    assert concepts["passed"] == 9 and concepts["cases"] == 10
    assert concepts["median_ms"] == 550 and concepts["p90_ms"] == 900
    assert summary["failed"] == [{"id": "c0", "failures": ["route: x"]}]
    assert summary["load_errors"] == ["a.json:x: turns vacío"]


def test_compare_lists_newly_failing_and_newly_passing():
    previous = summarize([result("a", "x", True, 1), result("b", "x", False, 1)], [], False)
    current = summarize([result("a", "x", False, 1), result("b", "x", True, 1), result("c", "x", False, 1)], [], False)
    assert compare(current, previous) == {"newly_failing": ["a"], "newly_passing": ["b"]}
    assert compare(current, None) == {"newly_failing": [], "newly_passing": []}


def test_markdown_mentions_live_backend_only_when_live():
    live = to_markdown(summarize([result("a", "x", True, 1)], [], True), {"newly_failing": [], "newly_passing": []})
    idle = to_markdown(summarize([result("a", "x", True, 1)], [], False), {"newly_failing": [], "newly_passing": []})
    assert "Latencias medidas con el backend activo." in live
    assert "backend activo" not in idle


def test_main_with_mock_writes_json_and_md(tmp_path, monkeypatch):
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    (cases_dir / "a.json").write_text(json.dumps([{"id": "s1", "category": "social",
                                                   "turns": [{"message": "hola", "expect": {"route": "social"}}]}]),
                                      encoding="utf-8")
    results_dir = tmp_path / "results"
    monkeypatch.setattr(run_eval, "RESULTS_DIR", results_dir)
    monkeypatch.setattr(run_eval, "WORK_ROOT", tmp_path / "work")
    monkeypatch.setattr(run_eval, "backend_live", lambda: False)
    assert run_eval.main(["--provider", "mock", "--cases-dir", str(cases_dir)]) == 0
    written = sorted(path.suffix for path in results_dir.iterdir())
    assert written == [".json", ".md"]
    report = json.loads(next(results_dir.glob("*.json")).read_text(encoding="utf-8"))
    assert report["summary"]["total"]["cases"] == 1
    assert not (tmp_path / "work").exists() or not any((tmp_path / "work").iterdir())


def test_the_mock_assistant_never_searches_the_web(tmp_path):
    assistant, _ = run_eval.build_assistant("mock", tmp_path)
    assert asyncio.run(assistant.web_search.search("última versión de Windows")) == []


def test_build_assistant_never_writes_storage(tmp_path, monkeypatch):
    original = tmp_path / "root" / "storage" / "knowledge" / "knowledge.sqlite3"
    original.parent.mkdir(parents=True)
    with closing(sqlite3.connect(original)) as db:
        db.execute("CREATE TABLE x(a)")
        db.commit()
    before = (original.stat().st_mtime_ns, original.stat().st_size)
    monkeypatch.setattr(run_eval, "PROJECT_ROOT", tmp_path / "root")
    work = tmp_path / "work"
    assistant, documents = run_eval.build_assistant("ollama", work)
    assert assistant.knowledge.root == work
    assert documents == {}
    assert (original.stat().st_mtime_ns, original.stat().st_size) == before
    assert sorted(path.name for path in original.parent.iterdir()) == ["knowledge.sqlite3"]
