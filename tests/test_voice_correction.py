"""Voz 2: post-transcription correction against known titles and headings (provisional thresholds)."""
import pytest

from backend.app.voice.correction import (ANCHORED_MIN, MARGIN, UNANCHORED_MIN, apply_to_result, correct,
                                          similarity, sounds)
from backend.app.voice.vocabulary import build

# The authorized titles and headings of the real knowledge base (read-only, 2026-11-18).
VOCABULARY = build(
    ["Training Setup Guideline", "Procedimiento de Reinicio", "Guía de Instalación", "Procedimiento de Emergencia",
     "Manual de Soporte VPN Phase1B"],
    ["COMPUTER PREPARATION", "WINDOWS DEPLOYMENT PROCEDURE", "GRAPHENE STATIONS DEPLOYMENT PROCEDURE",
     "REQUIRED PERIPHERALS", "APPS INSTALLATION", "CONFIRMS THE CPU FRECUENCY"],
)


def test_provisional_thresholds():
    assert (ANCHORED_MIN, UNANCHORED_MIN, MARGIN) == (0.55, 0.90, 0.10)


@pytest.mark.parametrize("heard", ["30 setos guillellidos", "30 en Serra, Vaglan"])
def test_misheard_title_after_the_anchor_is_corrected(heard):
    text, made = correct(f"Según el manual de {heard}, cuáles son los pasos de COMPUTER PREPARATION", VOCABULARY)
    assert text == "Según el manual de Training Setup Guideline, cuáles son los pasos de COMPUTER PREPARATION"
    assert len(made) == 1 and made[0].anchored and made[0].replacement == "Training Setup Guideline"
    assert made[0].original == heard.rstrip(",") and made[0].score >= ANCHORED_MIN


@pytest.mark.parametrize("heard", ["30 cero", "30 CERA", "30 en Serra"])
def test_too_little_left_of_the_title_is_not_guessed(heard):
    original = f"Según el manual de {heard}, cuáles son los pasos"
    assert correct(original, VOCABULARY) == (original, [])


@pytest.mark.parametrize(("heard", "expected"), [
    ("según el procedimiento de reinisio qué hago primero", "según el Procedimiento de Reinicio qué hago primero"),
    ("según el procedimiento de emergensia a quién llamo", "según el Procedimiento de Emergencia a quién llamo"),
    ("muéstrame la guía de instalasion", "muéstrame la Guía de Instalación"),
    ("cuáles son los pasos de computer preparación", "cuáles son los pasos de COMPUTER PREPARATION"),
    ("En el manual de soporte VPN Phase 1B, cómo configuro la VPN",
     "En el Manual de Soporte VPN Phase1B, cómo configuro la VPN"),
])
def test_near_misses_of_titles_and_headings_are_fixed(heard, expected):
    assert correct(heard, VOCABULARY)[0] == expected


@pytest.mark.parametrize("text", [
    "Según el manual de Server Rack Guide, cómo se monta un servidor",       # title that does not exist
    "Según la guía de Network Cabling Standards, qué cable uso",
    "Según el manual de usuario de la impresora cómo cambio el tóner",
    "mi computadora no prende",
    "Según internet, cuál es la versión más reciente de Windows 11.",
    "Según el manual de Training Setup Guideline, cuáles son los pasos de COMPUTER PREPARATION",  # already right
])
def test_unknown_titles_ordinary_speech_and_correct_text_stay_unchanged(text):
    assert correct(text, VOCABULARY) == (text, [])


def test_known_false_positive_a_partial_title_maps_to_the_existing_one():
    # Documented risk: "Manual de Soporte VPN" (not a title) becomes the existing Phase1B manual; raw_text keeps it.
    text, made = correct("En el manual de soporte VPN, cómo configuro la VPN", VOCABULARY)
    assert text == "En el Manual de Soporte VPN Phase1B, cómo configuro la VPN"
    assert made[0].original == "manual de soporte VPN"


def test_a_single_word_or_an_acronym_is_never_replaced():
    for text in ("Guideline", "configura la VPN", "revisa el SSD y la BIOS"):
        assert correct(text, VOCABULARY) == (text, [])


def test_result_keeps_the_original_and_log_fields_never_carry_the_transcript():
    result = apply_to_result({"text": "Según el manual de 30 setos guillellidos, cuáles son los pasos",
                              "engine": "whisper-small"}, VOCABULARY)
    assert result["text"] == "Según el manual de Training Setup Guideline, cuáles son los pasos"
    assert result["raw_text"] == "Según el manual de 30 setos guillellidos, cuáles son los pasos"
    assert result["corrections"][0]["replacement"] == "Training Setup Guideline"
    from backend.app.voice.correction import correct as _correct
    fields = _correct(result["raw_text"], VOCABULARY)[1][0].log_fields()
    assert set(fields) == {"replacement", "score", "anchored"}
    unchanged = apply_to_result({"text": "hola", "raw_text": "hola"}, VOCABULARY)
    assert unchanged["text"] == "hola" and unchanged["corrections"] == []


def test_sounds_and_similarity_helpers():
    assert sounds("30 setos") == "treinta setos"
    assert similarity("Training Setup Guideline", "training setup guideline") == 1.0
    assert similarity("hola", "Training Setup Guideline") < 0.5
