"""Modo voz: utilidades puras (canal, recorte de la respuesta corta, ofertas y lectura por partes)."""
import pytest

from backend.app.assistant import voice_mode
from backend.app.assistant.speech import REST, spoken_summary
from backend.app.assistant.voice_mode import (REST_LIMIT, REST_OFFER, VOICE_MAX_TOKENS, VOICE_OFFER, VOICE_RULES,
                                              channel_for, declines, split_spoken, trim_short, voice_speech,
                                              wants_detail, wants_rest)
from backend.app.providers.ollama import TRUNCATED_NOTE


def test_constants_are_the_spec_values():
    assert VOICE_OFFER == "¿Quieres más detalle?"
    assert REST_OFFER == "¿Sigo?"
    assert VOICE_MAX_TOKENS == 160
    assert REST_LIMIT == 1200
    assert "frases" in VOICE_RULES and "listas" in VOICE_RULES


def test_channel_for():
    assert channel_for("abc") == "voice"
    assert channel_for(None) == "text"
    assert channel_for("") == "text"


def test_trim_short_drops_the_truncation_note_and_cuts_at_a_sentence():
    assert trim_short("Una VPN cifra. Crea un tú\n\n" + TRUNCATED_NOTE) == "Una VPN cifra."
    assert trim_short("Una VPN cifra tu conexión.") == "Una VPN cifra tu conexión."
    assert trim_short("Una VPN cifra tu conexión y") == "Una VPN cifra tu conexión y."
    assert "recortada" not in trim_short("Algo largo sin punto\n\n" + TRUNCATED_NOTE)


@pytest.mark.parametrize("message", ["sí", "Sí.", "si", "claro", "ok", "explícame más", "Más detalle", "dime más",
                                     "cuéntame más", "sí, por favor", "continúa"])
def test_wants_detail(message):
    assert wants_detail(message)


@pytest.mark.parametrize("message", ["sí", "sigue", "Continúa.", "sigue leyendo", "y luego"])
def test_wants_rest(message):
    assert wants_rest(message)


@pytest.mark.parametrize("message", ["no", "No, gracias", "ahora no", "después"])
def test_declines(message):
    assert declines(message)
    assert not wants_detail(message) and not wants_rest(message)


@pytest.mark.parametrize("message", ["¿qué es DNS?", "sí, y qué es DNS", "dame los pasos de la VPN", "nose"])
def test_other_messages_are_none_of_them(message):
    assert not wants_detail(message) and not wants_rest(message) and not declines(message)


def test_split_spoken_cuts_at_last_sentence_before_limit():
    text = " ".join(f"Frase número {n}." for n in range(100))
    head, rest = split_spoken(text, limit=200)
    assert len(head) <= 200 and head.endswith(".")
    assert (head + " " + rest).split() == text.split()
    assert split_spoken("Corto.", limit=200) == ("Corto.", "")


def test_split_spoken_cleans_markdown_and_links():
    head, rest = split_spoken("**Hola** [sitio](https://example.com).", limit=200)
    assert head == "Hola sitio." and rest == ""


def test_voice_speech_keeps_procedures_as_today():
    answer = "«Reinicio» (3 pasos):\n1. Apagar el servidor.\n2. Esperar 30 segundos.\n3. Encender el servidor."
    assert voice_speech(answer) == (spoken_summary(answer), "")


def test_voice_speech_never_says_rest_is_in_the_chat():
    text = " ".join(f"Esta es la frase {n} de una explicación larga." for n in range(60))
    speech, rest = voice_speech(text)
    assert speech.endswith(REST_OFFER)
    assert REST not in speech
    assert rest
    head = speech[: -len(REST_OFFER)].strip()
    assert (head + " " + rest).split() == text.split()


def test_voice_speech_medium_answer_is_read_whole():
    text = " ".join(f"Frase {n}." for n in range(80))  # más de MAX_SPEECH, menos de REST_LIMIT
    assert 450 < len(text) < REST_LIMIT
    assert voice_speech(text) == (text, "")


def test_voice_speech_always_says_what_to_read():
    # Never None: with None the app caps the answer at 450 characters and says "El resto está en el chat."
    assert voice_speech("Una VPN cifra tu conexión.") == ("Una VPN cifra tu conexión.", "")
    lines = "\n".join(f"Línea {n} sin punto final" for n in range(18))  # ~440 here, more in the app
    speech, rest = voice_speech(lines)
    assert speech and rest == ""


def test_voice_speech_never_reads_the_truncation_note():
    speech, rest = voice_speech("Una explicación larga.\n\n" + TRUNCATED_NOTE)
    assert "recortada" not in speech and "recortada" not in rest
