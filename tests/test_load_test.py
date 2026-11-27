"""Prueba de carga (evals/load_test.py): los técnicos simulados, el resumen y el aislamiento, con el modelo de prueba
y en proceso (sin Ollama, sin puertos)."""
import asyncio
import os

import httpx
import pytest

from backend.app.history import conversations, store as history
from backend.app.main import create_app
from backend.app.providers.mock import MockLLM
from evals import load_test
from evals.run_eval import OfflineWebSearch
from test_assistant_service import StubKnowledge


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(history, "_ready", set())
    for name in (conversations.ENABLED_VARIABLE, conversations.GAP_VARIABLE):
        monkeypatch.delenv(name, raising=False)


def test_each_technician_has_an_own_number_and_asks_it_back():
    lines = load_test.script(2, 3, ["Manual A"])
    assert load_test.badge(2) in lines[0] and "número" in lines[-1]
    assert len(lines) == 5 and "Manual A" in lines[3]
    assert "Manual" not in " ".join(load_test.script(2, 3, []))
    assert len({load_test.badge(index) for index in range(20)}) == 20


def test_another_technicians_number_in_an_answer_is_a_mix():
    everyone = [load_test.badge(index) for index in range(3)]
    assert load_test.mixed_with(f"Tu número es {everyone[0]}.", everyone[0], everyone) == []
    assert load_test.mixed_with(f"Es {everyone[1]}", everyone[0], everyone) == [everyone[1]]
    assert load_test.mixed_with(None, everyone[0], everyone) == []


def test_summary_counts_errors_times_and_memory():
    results = [{"turns": [{"status": 200, "ms": 1000, "error": None}, {"status": 503, "ms": 5, "error": "busy"}],
                "remembers": True, "mixed": [], "foreign_history": 0},
               {"turns": [{"status": 200, "ms": 3000, "error": None}, {"status": "timeout", "ms": 9, "error": "timeout"}],
                "remembers": False, "mixed": ["4037"], "foreign_history": 1}]
    row = load_test.summarize(2, results, 4.0)
    assert row["questions"] == 4 and row["ok"] == 2
    assert row["errors"] == {"busy": 1, "timeout": 1}
    assert row["p50_s"] == 2.0 and row["max_s"] == 3.0
    assert (row["remember"], row["mixed"], row["foreign_history"]) == (1, 1, 1)
    assert "| 2 | 4 | 2 | busy×1, timeout×1 |" in load_test.to_markdown([row], {"started": "x", "model": "m",
                                                                          "turns": 1, "manuals": 0})


def test_the_isolated_server_never_inherits_the_real_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("ITGLASSES_API_TOKENS", "secreto")
    monkeypatch.setenv("ITGLASSES_PAIRING_ORIGIN", "https://172.16.5.142:8443")
    environment = load_test.server_environment(tmp_path, 8010)
    assert "ITGLASSES_API_TOKENS" not in environment and "ITGLASSES_PAIRING_ORIGIN" not in environment
    assert environment["ITGLASSES_PORT"] == "8010"
    for name in ("ITGLASSES_PAIRING_DB", "ITGLASSES_HISTORY_DB", "ITGLASSES_BACKUP_DIR"):
        assert environment[name].startswith(str(tmp_path))
    assert environment["ITGLASSES_DAILY_BACKUP"] == "0"


def test_never_on_port_8000():
    with pytest.raises(SystemExit):
        asyncio.run(load_test.main_async(load_test.parse(["--port", "8000"])))


def test_several_technicians_at_once_keep_their_own_conversations():
    made = load_test.create_accounts({"ITGLASSES_PAIRING_DB": os.environ["ITGLASSES_PAIRING_DB"]}, 3)
    from backend.app.assistant.service import UnifiedAssistantService
    app = create_app(MockLLM(), knowledge=StubKnowledge())
    # Sin internet ni manuales reales: solo cuenta que cada técnico tenga lo suyo.
    app.state.assistant = UnifiedAssistantService(app.state.knowledge, app.state.provider, web_search=OfflineWebSearch())
    base = "http://127.0.0.1:8000"

    async def scenario():
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport) as client:
            owned = {}
            first, _ = await load_test.run_level(client, base, made, 3, 2, [], owned)
            again, results = await load_test.run_level(client, base, made, 2, 1, [], owned)
            return first, again, results, owned

    first, again, results, owned = asyncio.run(scenario())
    assert first["questions"] == 3 * 4 and first["ok"] == first["questions"], first
    assert again["ok"] == again["questions"] == 2 * 3
    assert first["foreign_history"] == again["foreign_history"] == 0
    assert all(len(owned[name]) == (2 if index < 2 else 1) for index, (name, _) in enumerate(made))
    assert all(turn["session_id"] for result in results for turn in result["turns"])


def test_the_page_origin_follows_the_configured_port(monkeypatch):
    """Los manuales aceptan la página del backend en su propio puerto (8000; la copia de la prueba usa 8010)."""
    from dataclasses import replace
    from types import SimpleNamespace

    from backend.app.core import config
    from backend.app.pairing.store import permitted_browser_origin

    def page(origin):
        return SimpleNamespace(headers={"origin": origin}, url=SimpleNamespace(scheme="http", netloc="x"))

    assert permitted_browser_origin(page("http://127.0.0.1:8000"))
    assert not permitted_browser_origin(page("http://127.0.0.1:8010"))
    monkeypatch.setattr(config, "settings", replace(config.settings, port=8010))
    assert permitted_browser_origin(page("http://127.0.0.1:8010"))
    assert permitted_browser_origin(page("http://localhost:8010"))
    assert not permitted_browser_origin(page("http://127.0.0.1:8000"))
    assert not permitted_browser_origin(page("http://evil.test"))
