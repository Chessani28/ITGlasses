"""Cuándo buscar en internet: hechos que cambian con el tiempo (precio, caídas, soporte vigente, noticias), no solo
"versión" o "última". Las preguntas de concepto nunca salen a internet."""
import json
from pathlib import Path

import pytest

from backend.app.assistant.service import UnifiedAssistantService

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("message", [
    "¿Windows 10 todavía tiene soporte de Microsoft?",
    "¿Cuánto cuesta hoy un SSD de 1 TB?",
    "¿Microsoft Teams tiene una caída hoy?",
    "¿Está caído Outlook?",
    "¿Cuál es el precio de una licencia de Office?",
    "¿Cuáles son las noticias de ciberseguridad de esta semana?",
    "¿Cuándo termina el soporte de Windows 11 23H2?",
    "Is Zoom down right now?",
    "¿Cuál es la versión más reciente de Windows 11?",
    "Busca en internet qué es Microsip",
])
def test_time_sensitive_questions_search_the_web(message):
    assert UnifiedAssistantService._needs_web(message)


def concept_messages():
    cases = json.loads((ROOT / "evals" / "cases" / "conceptos.json").read_text(encoding="utf-8"))
    return [turn["message"] for case in cases for turn in case["turns"]]


@pytest.mark.parametrize("message", concept_messages() + [
    "¿Qué es el soporte técnico?",
    "¿Cómo reinicio el router?",
    "¿Por qué mi laptop está lenta?",
    "Dame los pasos para instalar la impresora",
])
def test_concepts_and_how_tos_stay_offline(message):
    assert not UnifiedAssistantService._needs_web(message)
