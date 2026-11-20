"""El texto que llega a Piper debe sonar natural."""
from backend.app.voice.normalize import normalize_for_speech


def test_source_tags_and_document_text_are_not_spoken():
    answer = ("Paso 2 de «WINDOWS DEPLOYMENT»: Apaga la computadora.\n"
              "Texto del documento: Then turn off the computer.\n"
              "[Fuente: Training Setup Guideline, página 3, versión 1]")
    assert normalize_for_speech(answer) == "Paso 2 de WINDOWS DEPLOYMENT: Apaga la computadora."


def test_list_lines_become_sentences():
    answer = "«Preparación» (2 pasos):\n1. Conecta el cable\n2. Enciende el equipo"
    assert normalize_for_speech(answer) == "Preparación (2 pasos): Conecta el cable. Enciende el equipo."


def test_links_and_bare_urls_are_dropped():
    answer = "- [Python.org](https://python.org): Python 3.14\nMás en https://docs.python.org/3/."
    assert normalize_for_speech(answer) == "Python.org: Python 3.14. Más en."


def test_markdown_tables_are_read_as_phrases():
    answer = "| Puerto | Uso |\n|---|---|\n| 443 | HTTPS |"
    assert normalize_for_speech(answer) == "Puerto, Uso. 443, HTTPS."


def test_plain_sentence_is_unchanged():
    assert normalize_for_speech("Reinicia el router y espera un minuto.") == "Reinicia el router y espera un minuto."


def test_citation_ids_are_still_spoken_as_reference():
    assert "referencia" in normalize_for_speech("Ver [b707569d-1234-5678].")


def test_project_config_file_is_complete_and_valid():
    from backend.app.core.config import PROJECT_ROOT, load_settings
    import tomllib
    data = tomllib.loads((PROJECT_ROOT / "config.toml").read_text(encoding="utf-8"))
    assert {"model", "max_output_tokens", "keep_alive", "top_k"} <= set(data)
    settings = load_settings()
    assert settings.model == data["model"]
    assert settings.max_output_tokens == data["max_output_tokens"]
