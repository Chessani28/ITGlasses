"""Examen del cerebro: carga de casos y revisiones automáticas (sin modelos)."""
import json

from evals.cases import load_cases
from evals.checks import KNOWN_CHECKS, check_turn


def write(tmp_path, name, cases):
    path = tmp_path / name
    path.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    return tmp_path


CASE = {"id": "c1", "category": "conceptos",
        "turns": [{"message": "¿Qué es una VPN?", "expect": {"route": "general"}}, {"message": "sí"}]}


def test_load_cases_reads_valid_cases(tmp_path):
    cases, errors = load_cases([write(tmp_path, "a.json", [CASE])])
    assert errors == []
    assert len(cases) == 1
    case = cases[0]
    assert case.channel == "text"
    assert [turn.message for turn in case.turns] == ["¿Qué es una VPN?", "sí"]
    assert case.turns[1].expect is None
    assert case.file == "a.json"


def test_load_cases_reports_bad_cases_and_keeps_the_rest(tmp_path):
    bad_turns = {"id": "vacio", "category": "x", "turns": []}
    bad_check = {"id": "raro", "category": "x", "turns": [{"message": "hola", "expect": {"nope": 1}}]}
    cases, errors = load_cases([write(tmp_path, "b.json", [bad_turns, bad_check, CASE])])
    assert [case.id for case in cases] == ["c1"]
    assert len(errors) == 2
    assert any("b.json" in error and "vacio" in error for error in errors)
    assert any("b.json" in error and "raro" in error and "nope" in error for error in errors)


def test_a_file_that_is_not_json_is_an_error(tmp_path):
    (tmp_path / "roto.json").write_text("{", encoding="utf-8")
    cases, errors = load_cases([write(tmp_path, "a.json", [CASE])])
    assert len(cases) == 1
    assert len(errors) == 1 and "roto.json" in errors[0]


def test_duplicate_ids_are_an_error(tmp_path):
    cases, errors = load_cases([write(tmp_path, "a.json", [CASE, CASE])])
    assert len(cases) == 1
    assert len(errors) == 1 and "c1" in errors[0]


def test_known_checks_are_the_spec_list():
    assert KNOWN_CHECKS == frozenset({
        "route", "contains_all", "contains_any", "not_contains", "max_chars", "min_chars", "max_speech_chars",
        "max_sentences", "ends_with_offer", "has_sources", "source_document", "steps_verbatim", "web_used"})


def test_route_and_text_checks():
    result = {"route": "general", "answer": "Crea un túnel cifrado.", "speech": None, "sources": []}
    expect = {"route": "general", "contains_any": ["tunel"], "contains_all": ["CIFRADO"],
              "not_contains": ["El resto está en el chat"]}
    assert check_turn(expect, result, {}) == []
    failures = check_turn({**expect, "route": "knowledge"}, result, {})
    assert len(failures) == 1 and "route" in failures[0]
    assert check_turn({"not_contains": ["TUNEL"]}, result, {})
    assert check_turn({"contains_any": ["dns", "dhcp"]}, result, {})


def test_length_and_sentence_checks():
    result = {"route": "general", "answer": "Uno. Dos. Tres.", "speech": None, "sources": []}
    assert check_turn({"max_chars": 100, "min_chars": 5}, result, {}) == []
    assert check_turn({"max_chars": 5}, result, {})
    assert check_turn({"min_chars": 100}, result, {})
    assert check_turn({"max_sentences": 3}, result, {}) == []
    assert check_turn({"max_sentences": 2}, result, {})


def test_speech_checks_use_speech_or_answer():
    spoken = {"answer": "x" * 100, "speech": "corto", "sources": []}
    assert check_turn({"max_speech_chars": 20}, spoken, {}) == []
    unspoken = {"answer": "x" * 100, "speech": None, "sources": []}
    assert check_turn({"max_speech_chars": 20}, unspoken, {})


def test_ends_with_offer():
    for text in ("Es un túnel. ¿Quieres más detalle?", "Primera parte. ¿Sigo?"):
        assert check_turn({"ends_with_offer": True}, {"answer": text, "speech": None, "sources": []}, {}) == []
    assert check_turn({"ends_with_offer": True}, {"answer": "Es un túnel.", "speech": None, "sources": []}, {})


def test_sources_and_verbatim_steps():
    answer = "«VPN» (2 pasos):\n1. Abre   el cliente.\n2. Pulsa conectar."
    result = {"answer": answer, "speech": None, "sources": [{"document": "VPN", "page": 1}]}
    documents = {"VPN": "Instrucciones. Abre el cliente. Luego: Pulsa conectar. Fin."}
    assert check_turn({"has_sources": True, "source_document": "VPN", "steps_verbatim": True},
                      result, documents) == []
    assert check_turn({"source_document": "Correo"}, result, documents)
    assert check_turn({"has_sources": True}, {**result, "sources": []}, documents)
    invented = {**result, "answer": answer + "\n3. Reinicia el servidor."}
    assert check_turn({"steps_verbatim": True}, invented, documents)


def test_web_used():
    web = {"answer": "x", "speech": None, "sources": [{"title": "t", "url": "https://example.com"}]}
    assert check_turn({"web_used": True}, web, {}) == []
    assert check_turn({"web_used": True}, {**web, "sources": [{"document": "VPN"}]}, {})
