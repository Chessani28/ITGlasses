"""The product name, as Whisper and Vosk heard it in real recordings ("Oye ITGlasses…" → "Oye, ¿y qué clases?…",
"porque y clases…"): fixed after the greeting; never elsewhere."""
import pytest

from backend.app.voice.correction import correct
from backend.app.voice.vocabulary import FIXED_TERMS, build


def fix(text):
    return correct(text, build([], []))[0]


@pytest.mark.parametrize("heard", [
    "Oye, ¿y qué clases? ¿Puedes ayudarme con mi computadora?",
    "oye y clases puedes ayudarme con mi computadora",
    "Oye it glasses, puedes ayudarme con mi computadora",
    "Hola, y qué clases, puedes ayudarme con mi computadora",
    "Ok ITGlasses puedes ayudarme con mi computadora",
])
def test_the_name_after_a_greeting_is_fixed(heard):
    fixed = fix(heard)
    assert "ITGlasses" in fixed and "clases" not in fixed.casefold() and "glasses," not in fixed.split("ITGlasses")[0]
    assert fixed.casefold().endswith("ayudarme con mi computadora") or "ayudarme con mi computadora" in fixed


@pytest.mark.parametrize("text", [
    "¿Qué clases de VPN hay?",
    "Dame las clases de servidores",
    "Oye, ¿qué es una VPN?",
])
def test_ordinary_speech_is_untouched(text):
    assert fix(text) == text


def test_the_name_is_a_whisper_hint():
    assert "ITGlasses" in FIXED_TERMS


@pytest.mark.parametrize("heard,expected", [
    ("AS Active Directory", "¿Qué es Active Directory"),
    ("KS Active Directory", "¿Qué es Active Directory"),
    ("CES, Una Dirección IP", "¿Qué es una Dirección IP"),
    ("Kes una VPN?", "¿Qué es una VPN?"),
])
def test_a_misheard_que_es_at_the_start_is_fixed(heard, expected):
    assert fix(heard) == expected


@pytest.mark.parametrize("text", [
    "As de bastos", "Es una VPN corporativa", "Ase falta reiniciar", "ASUS no enciende", "No puedo hacer un ping",
])
def test_other_starts_are_untouched(text):
    assert fix(text) == text


def test_the_fix_is_reported_as_a_correction():
    text, made = correct("Oye, ¿y qué clases? ¿Puedes ayudarme?", build([], []))
    assert text.startswith("Oye, ITGlasses") and made and made[0].replacement == "ITGlasses"
