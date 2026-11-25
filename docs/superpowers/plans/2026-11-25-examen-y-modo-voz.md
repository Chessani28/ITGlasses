# Examen del cerebro y modo voz — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un examen repetible del cerebro (casos JSON + corredor + reporte) y un modo voz que da respuestas generales
cortas en dispositivos, con detalle a demanda y "¿Sigo?" en vez de "El resto está en el chat".

**Architecture:** `evals/` es un paquete aparte que arma `UnifiedAssistantService` en proceso contra una copia del índice
de manuales en `.cache/eval/`. El modo voz vive en un módulo puro nuevo (`backend/app/assistant/voice_mode.py`); el
asistente recibe `channel` desde `history/chat.py` (por `request.state.device_id`) y devuelve su propio `speech` en voz.

**Tech Stack:** Python 3.13, FastAPI, pytest, httpx (MockTransport en pruebas), Ollama, JSON. Sin dependencias nuevas.

**Spec:** `docs/superpowers/specs/2026-11-25-examen-y-modo-voz-design.md`

## Global Constraints

- Rama `feature/examen-modo-voz` (ya creada desde `feature/device-registration`). Nunca `master`.
- No instalar nada; no tocar `.runtime\`, `runtime-config.psd1`, `.env`, certificados ni tokens.
- No correr el examen con Ollama (`--provider ollama`), `smoke_*.py` ni `test_voice_real.py` sin permiso del dueño.
- El examen nunca escribe en `storage/` ni usa el puerto 8000 (solo lo consulta con GET `/health`).
- `pytest` y `node --test` no deben requerir Ollama ni GPU.
- Textos que ve u oye el usuario en español. Ofertas exactas: `"¿Quieres más detalle?"` y `"¿Sigo?"`.
- Voz: respuesta general corta ≤ ~40 palabras (1–2 frases), `VOICE_MAX_TOKENS = 160`; lectura larga hasta
  `REST_LIMIT = 1200` caracteres por turno.
- Canal text (web) se comporta exactamente como hoy: las pruebas existentes pasan sin cambiarlas.
- Procedimientos de manuales en voz: idénticos a hoy (`spoken_summary` los lee completos).
- Privacidad *(decisión del plan, no del spec)*: los casos de manuales citan contenido interno de la empresa, así que
  van en `evals/cases/local/` (no versionado). Los demás casos sí se versionan.
- Comandos de prueba (PowerShell, desde `D:\ITGlasses`):
  `$env:TEMP='D:\ITGlasses\.cache\tmp'; $env:TMP=$env:TEMP; .\.venv\Scripts\python.exe -m pytest -q -o cache_dir=.cache/pytest`
  y `node --test "tests/*.test.mjs"`.

## Review Focus

1. Un "sí" que responde a otra oferta (`pending_general`, `pending_alternative`, `pending_section`) no debe disparar
   `pending_detail`: solo puede haber una oferta activa; la nueva reemplaza a las otras. → prueba en Task 6.
2. La respuesta corta en voz llega cortada por el límite de tokens (con `TRUNCATED_NOTE`): se quita la nota y se corta en
   la última frase completa; nunca se oye "Respuesta recortada". → prueba en Task 4.
3. Un dispositivo sin dueño (destino legado) también recibe modo voz. → prueba en Task 7.
4. Respuesta de error fija en voz (`GENERAL_TIMEOUT`, `GENERAL_NOT_RUNNING`…) no lleva "¿Quieres más detalle?". →
   prueba en Task 6.
5. Un caso JSON mal formado (clave de revisión desconocida, `turns` vacío) se reporta como error de carga con su
   archivo e id, sin tumbar el resto del examen. → prueba en Task 1.

---

### Task 1: Casos y revisiones del examen

**Files:**
- Create: `evals/__init__.py`, `evals/cases.py`, `evals/checks.py`
- Test: `tests/test_eval_checks.py`

**Interfaces:**
- Produces:
  - `evals.cases.Case` (dataclass): `id: str, category: str, channel: str ("text"|"voice"), turns: list[Turn], file: str`
  - `evals.cases.Turn` (dataclass): `message: str, expect: dict | None`
  - `evals.cases.load_cases(paths: list[Path]) -> tuple[list[Case], list[str]]` — casos válidos y errores
    (`"<archivo>:<id>: <motivo>"`). Lee cada `*.json` (lista de casos) de los directorios dados.
  - `evals.checks.KNOWN_CHECKS: frozenset[str]` = `route, contains_all, contains_any, not_contains, max_chars,
    min_chars, max_speech_chars, max_sentences, ends_with_offer, has_sources, source_document, steps_verbatim,
    web_used`.
  - `evals.checks.check_turn(expect: dict, result: dict, document_texts: dict[str, str]) -> list[str]` — motivos de
    fallo (vacío = pasó). `result` es el dict del asistente más `speech`. `document_texts` mapea título de documento →
    texto completo (para `steps_verbatim`).

- [ ] **Step 1: Escribir las pruebas que fallan** en `tests/test_eval_checks.py`:
  - `test_load_cases_reads_valid_cases` — archivo temporal con un caso de dos turnos → 1 caso, `channel` por defecto
    `"text"`, 0 errores.
  - `test_load_cases_reports_bad_cases_and_keeps_the_rest` — un caso con `turns: []`, otro con
    `expect: {"nope": 1}`, otro válido → 1 caso, 2 errores que contienen el nombre del archivo y el id.
  - `test_duplicate_ids_are_an_error`.
  - `test_route_and_text_checks` — `{"route":"general","contains_any":["tunel"],"not_contains":["El resto está en el chat"]}`
    contra `{"route":"general","answer":"Crea un túnel cifrado."}` → `[]` (comparación sin acentos ni mayúsculas);
    con `route:"knowledge"` → un motivo que menciona `route`.
  - `test_length_and_sentence_checks` — `max_chars`, `min_chars`, `max_sentences: 2` contra un texto de 3 frases → falla.
  - `test_speech_checks_use_speech_or_answer` — `max_speech_chars: 20` usa `speech` si no es `None`, si no `answer`.
  - `test_ends_with_offer` — pasa si el texto oído termina en `"¿Quieres más detalle?"` o `"¿Sigo?"`.
  - `test_sources_and_verbatim_steps` — `has_sources` + `source_document:"VPN"` con `sources:[{"document":"VPN"}]`
    pasa; `steps_verbatim` falla si un paso numerado de `answer` no aparece literal (normalizado de espacios) en
    `document_texts["VPN"]`.
  - `test_web_used` — pasa si `result["sources"]` tiene alguna URL `http`.
- [ ] **Step 2: Correr** `... -m pytest -q tests\test_eval_checks.py` → FAIL (módulo inexistente).
- [ ] **Step 3: Implementar** `evals/cases.py` y `evals/checks.py`. Normalizar con
  `backend.app.knowledge.store.normalize`. Contar frases con `re.split(r"(?<=[.!?…])\s+", texto.strip())`.
- [ ] **Step 4: Correr** el archivo → PASS.
- [ ] **Step 5: Commit** `"Examen: casos y revisiones"`.

### Task 2: Corredor y reporte

**Files:**
- Create: `evals/run_eval.py`, `evals/report.py`, `scripts/run-eval.ps1`
- Modify: `.gitignore` (añadir `evals/results/` y `evals/cases/local/`)
- Test: `tests/test_eval_runner.py`

**Interfaces:**
- Consumes: `load_cases`, `check_turn` (Task 1).
- Produces:
  - `evals.run_eval.build_assistant(provider_name: str, work_dir: Path) -> tuple[UnifiedAssistantService, dict[str, str]]`
    — para `"ollama"`: copia `storage/knowledge/knowledge.sqlite3` a `work_dir/storage/knowledge/` con la API
    `sqlite3.Connection.backup` (respeta WAL), crea `KnowledgeService(work_dir, OllamaEmbeddings(...), EvidenceSelector(...))`
    con los mismos argumentos que `knowledge/routes.py:service`, `OllamaProvider(settings)` y `WebSearchService()`.
    Para `"mock"`: `MockLLM()` y knowledge `None`. Devuelve también `document_texts` (título → texto de sus chunks).
  - `evals.run_eval.run_case(assistant, case: Case, document_texts) -> dict` — `{"id","category","passed","turns":[{"message","answer","speech","route","latency_ms","failures"}]}`.
    Sesión nueva por caso (`session_id=None` en el primer turno, luego el devuelto). Llama
    `assistant.chat(message, session_id, channel=case.channel)` y calcula `speech` como la ruta HTTP (Task 7 lo
    define: `result.get("speech")` si la clave existe, si no `spoken_summary(answer)`).
  - `evals.run_eval.main(argv: list[str] | None = None) -> int` — opciones `--provider mock|ollama` (defecto
    `ollama`), `--category`, `--case`, `--compare <ruta>` (defecto: el último `evals/results/*.json`), `--cases-dir`
    (repetible; defecto `evals/cases` y `evals/cases/local` si existe). Siempre devuelve 0 si corrió completo.
  - `evals.report.summarize(results: list[dict], load_errors: list[str], backend_live: bool) -> dict` y
    `evals.report.compare(current: dict, previous: dict | None) -> dict` (`{"newly_failing":[ids],"newly_passing":[ids]}`),
    `evals.report.to_markdown(summary: dict, comparison: dict) -> str`.
- Salida: `evals/results/<AAAAMMDD-HHMMSS>.json` y `.md`; resumen en consola: total, por categoría
  (aciertos/total, mediana y p90 de latencia), fallidos con motivo, cambios vs. la corrida comparada, errores de carga
  y, si `backend_live`, la línea `"Latencias medidas con el backend activo."`.
- `backend_live`: GET `http://127.0.0.1:8000/health` con timeout 1 s; cualquier error → `False`.
- `work_dir`: `.cache/eval/<marca de tiempo>/`, se borra al terminar (solo esa carpeta).

- [ ] **Step 1: Pruebas que fallan** en `tests/test_eval_runner.py` (usan `StubProvider`/`StubKnowledge` de
  `tests/test_assistant_service.py` y un asistente falso cuando haga falta):
  - `test_run_case_threads_the_session_and_measures_latency` — asistente falso que devuelve el mismo `session_id`;
    el segundo turno recibe el `session_id` del primero; cada turno tiene `latency_ms >= 0`.
  - `test_run_case_passes_the_channel` — el asistente falso registra `channel == "voice"` para un caso voice.
  - `test_summarize_groups_by_category_with_median_and_p90`.
  - `test_compare_lists_newly_failing_and_newly_passing`.
  - `test_markdown_mentions_live_backend_only_when_live`.
  - `test_main_with_mock_writes_json_and_md` — `--provider mock --cases-dir <tmp>`, con `RESULTS_DIR` parcheado a
    `tmp_path` y `backend_live` parcheado a `False` → devuelve 0 y escribe los dos archivos.
  - `test_build_assistant_never_writes_storage` — con `PROJECT_ROOT` parcheado a un tmp con una
    `storage/knowledge/knowledge.sqlite3` mínima, `build_assistant("ollama", work)` (sin red: solo construye) deja el
    archivo original con el mismo `mtime` y tamaño.
- [ ] **Step 2: Correr** → FAIL.
- [ ] **Step 3: Implementar** `evals/report.py` y `evals/run_eval.py` (ejecutable como
  `python -m evals.run_eval`). `scripts/run-eval.ps1`: fija `TEMP`/`TMP` como en `CLAUDE.md` y llama
  `.\.venv\Scripts\python.exe -m evals.run_eval @args`.
- [ ] **Step 4: Correr** → PASS, y la suite completa de pytest → PASS.
- [ ] **Step 5: Commit** `"Examen: corredor y reporte"`.

### Task 3: Banco de casos

**Files:**
- Create: `evals/cases/conceptos.json` (~25), `internet.json` (~15), `social.json` (~5), `conversaciones.json`
  (~15), `voz.json` (~10), `evals/README.md` (cómo correrlo y cómo escribir un caso)
- Create (no versionado): `evals/cases/local/manuales.json` (~30)

- [ ] **Step 1:** Escribir los casos versionados según la sección "Contenido del banco" del spec. Los de `voz.json` y
  los conceptos en `channel:"voice"` llevan `max_sentences: 3`, `max_speech_chars: 300`, `ends_with_offer: true` y un
  segundo turno `"sí"` con `min_chars: 300`; cada uno tiene su gemelo `channel:"text"` sin oferta
  (`not_contains: ["¿Quieres más detalle?"]`). Internet: `web_used: true`, sin fijar el dato.
- [ ] **Step 2:** Escribir `local/manuales.json` leyendo títulos y textos de los documentos indexados (consulta de solo
  lectura a la copia de `.cache/eval/`, nunca a `storage/`): pasos (`has_sources`, `source_document`,
  `steps_verbatim`), nombres, navegación ("el paso 3", "el siguiente", "repite") y 5 preguntas que los manuales no
  cubren (`not_contains` de pasos inventados).
- [ ] **Step 3:** `python -m evals.run_eval --provider mock` → corre completo y carga todos los casos sin errores de
  carga (los fallos con mock son esperados).
- [ ] **Step 4:** Pedir al dueño que revise `local/manuales.json` (lista de preguntas y respuestas esperadas).
- [ ] **Step 5: Commit** solo los casos versionados y el README: `"Examen: banco de casos"`.

### Task 4: Módulo de modo voz (puro)

**Files:**
- Create: `backend/app/assistant/voice_mode.py`
- Test: `tests/test_voice_mode.py`

**Interfaces:**
- Produces:
  - Constantes: `VOICE_OFFER = "¿Quieres más detalle?"`, `REST_OFFER = "¿Sigo?"`, `VOICE_MAX_TOKENS = 160`,
    `REST_LIMIT = 1200`, `VOICE_RULES` (texto del prompt: 1–2 frases, ≤ 40 palabras, sin listas, viñetas, markdown,
    URLs ni símbolos; números y siglas como se dicen; en español).
  - `channel_for(device_id: str | None) -> str` — `"voice"` si hay `device_id`, si no `"text"`.
  - `trim_short(answer: str) -> str` — quita `TRUNCATED_NOTE` (de `providers.ollama`) y, si quedó cortada, corta en la
    última frase completa; si no queda ninguna frase completa, devuelve el texto con `"."` al final de la última
    palabra.
  - `wants_detail(message: str) -> bool` — afirmaciones de `_AFFIRMATION` más "explícame más", "más detalle",
    "dime más", "cuéntame más", "amplía".
  - `wants_rest(message: str) -> bool` — afirmaciones más "sigue", "continúa", "sigue leyendo", "y luego".
  - `declines(message: str) -> bool` — mismo patrón que `_NO_THANKS`.
  - `split_spoken(text: str, limit: int = REST_LIMIT) -> tuple[str, str]` — texto limpio para oír (mismo `_clean`
    que `speech.py`, en una línea) partido en la última frase completa antes de `limit`; `("todo", "")` si cabe.
  - `voice_speech(answer: str) -> tuple[str | None, str]` — `(speech, resto)`. Si `spoken_summary(answer)` no es
    `None` y no termina en `speech.REST` → `(eso, "")` (procedimientos, listas, secciones: sin cambios). Si no:
    `split_spoken`; con resto → `(cabeza + " " + REST_OFFER, resto)`; sin resto y ≤ `speech.MAX_SPEECH` →
    `(None, "")` (la app lee `answer`); sin resto y más largo → `(texto, "")`.

- [ ] **Step 1: Pruebas que fallan** en `tests/test_voice_mode.py`:
  - `test_channel_for` (`"abc"` → `"voice"`, `None`/`""` → `"text"`).
  - `test_trim_short_drops_the_truncation_note_and_cuts_at_a_sentence` — `"Una VPN cifra. Crea un tú\n\n" + TRUNCATED_NOTE` → `"Una VPN cifra."`.
  - `test_wants_detail_wants_rest_and_declines` (parametrizado: "sí", "Sí.", "explícame más", "sigue", "no gracias",
    y "¿qué es DNS?" → todos `False`).
  - `test_split_spoken_cuts_at_last_sentence_before_limit`.
  - `test_voice_speech_keeps_procedures_as_today` — una respuesta `«X» (3 pasos):` con 3 pasos → igual a
    `spoken_summary`, resto `""`.
  - `test_voice_speech_never_says_rest_is_in_the_chat` — texto plano de 2000 caracteres → speech termina en
    `"¿Sigo?"`, no contiene `"El resto está en el chat"`, resto no vacío y `cabeza + resto` cubre todo el texto.
  - `test_voice_speech_short_answer_is_left_to_the_app` → `(None, "")`.
- [ ] **Step 2: Correr** → FAIL.
- [ ] **Step 3: Implementar.** Importar `_clean`, `MAX_SPEECH`, `REST`, `spoken_summary` de `speech.py` (no
  duplicar).
- [ ] **Step 4: Correr** → PASS.
- [ ] **Step 5: Commit** `"Modo voz: utilidades puras"`.

### Task 5: Límite de salida por llamada en Ollama

**Files:**
- Modify: `backend/app/providers/ollama.py` (`generate_general`, `generate_general_turns`)
- Test: `tests/test_ollama_general.py` (añadir)

**Interfaces:**
- Produces: `generate_general(message: str, num_predict: int | None = None) -> str` y
  `generate_general_turns(message: str, turns, num_predict: int | None = None) -> str`. `None` = comportamiento actual
  (`min(GENERAL_MAX_OUTPUT_TOKENS, context_length // 2)`); un número se usa tal cual (acotado al mismo máximo).

- [ ] **Step 1: Prueba que falla** `test_general_accepts_a_shorter_output_limit` — con `adapter(seen=...)`,
  `generate_general("x", num_predict=160)` envía `options.num_predict == 160`; sin argumento sigue enviando 768.
  Igual para `generate_general_turns`.
- [ ] **Step 2: Correr** → FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Correr** `tests\test_ollama_general.py` → PASS.
- [ ] **Step 5: Commit** `"Ollama: límite de salida por llamada"`.

### Task 6: Asistente con canal, respuesta corta, detalle y "¿Sigo?"

**Files:**
- Modify: `backend/app/assistant/service.py` (`chat`, `_chat`, `_general_answer`)
- Test: `tests/test_voice_answers.py`

**Interfaces:**
- Consumes: `voice_mode` (Task 4), `num_predict` (Task 5).
- Produces:
  - `UnifiedAssistantService.chat(message, session_id=None, channel="text") -> dict`. En canal `"voice"` el dict
    **siempre** trae la clave `"speech"` (`str | None`); en `"text"` no la trae (la ruta usa `spoken_summary` como hoy).
  - Estado: `state["_channel"]` (transitorio, no se guarda: empieza por `_`), `state["pending_detail"] =
    {"question": str}` y `state["pending_rest"] = {"text": str, "route": str}`.
- Comportamiento:
  - `_general_answer`: si `state.get("_channel") == "voice"` y no es una petición de detalle, cambia en el prompt
    "de forma breve y clara" por `VOICE_RULES`, llama `generate_general(_turns)` con `num_predict=VOICE_MAX_TOKENS`
    (si el proveedor solo tiene `generate`, la llama igual) y aplica `trim_short`. Marca `state["_voice_short"] = True`.
  - `chat()` en voz, tras `_chat`: si `route == "general"`, `_voice_short` y la respuesta no está en
    `GENERAL_FAILURES` ni es `GENERAL_OFFER` → `answer += "\n\n" + VOICE_OFFER` y `pending_detail = {"question": <mensaje
    original>}`. Luego `speech, resto = voice_speech(answer)`; con resto → `pending_rest = {"text": resto, "route": route}`.
    Al poner una oferta se quitan `pending_detail`, `pending_rest`, `pending_general`, `pending_alternative` y
    `pending_section` que no sean la nueva (una sola oferta activa). La respuesta guardada en `history` del estado no
    lleva la oferta (solo la del dict devuelto).
  - `_chat`, antes de `pending_general`: `pending_rest` + `wants_rest` → responde `pending_rest["text"]` con su `route`
    y `sources: []` (sin modelo). `pending_detail` + `wants_detail` → `_general_answer` de la pregunta guardada con
    `_channel` = `"text"` durante esa llamada (prompt y 768 tokens de hoy), `route: "general"`; su `speech` sale de
    `voice_speech` (sin oferta de detalle). Cualquiera de las dos + `declines` → `"De acuerdo."`, ruta `"social"`.
    Cualquier otro mensaje descarta ambas y sigue el flujo normal.

- [ ] **Step 1: Pruebas que fallan** en `tests/test_voice_answers.py` (con `build()`/`StubProvider` de
  `test_assistant_service.py`; un `RecordingGeneralProvider` con `generate_general(message, num_predict=None)` que
  registra prompt y límite):
  - `test_voice_general_answer_is_short_and_offers_detail` — "¿Qué es una VPN?" en voice: prompt contiene
    `VOICE_RULES`, `num_predict == 160`, respuesta termina en `"¿Quieres más detalle?"`, `"speech"` presente.
  - `test_text_channel_is_unchanged` — mismo mensaje en text: prompt sin `VOICE_RULES`, `num_predict is None`, sin
    oferta, sin clave `"speech"`.
  - `test_yes_after_the_offer_gives_the_long_version` — segundo turno "sí": prompt sin `VOICE_RULES`, contiene la
    pregunta original, sin oferta nueva.
  - `test_no_after_the_offer_closes_it` → `"De acuerdo."`, y un tercer "sí" ya no pide detalle.
  - `test_another_message_drops_the_offer`.
  - `test_long_voice_answer_offers_to_continue_and_reads_the_stored_rest` — proveedor que devuelve 2000 caracteres
    en la versión larga: `speech` termina en `"¿Sigo?"`; "sigue" devuelve exactamente el resto guardado y el
    proveedor no recibe una llamada nueva.
  - `test_voice_procedure_is_read_as_today` — con `StubStepsKnowledge`, el `speech` en voice es igual a
    `spoken_summary(answer)`.
  - `test_failure_messages_get_no_offer` — proveedor que lanza `ProviderTimeoutError` → `GENERAL_TIMEOUT` sin oferta,
    sin `pending_detail`.
  - `test_only_one_offer_is_active` — tras una oferta `pending_general` (`GENERAL_OFFER`) en voice, "sí" la responde
    como hoy y no crea `pending_detail` en ese turno.
  - `test_the_saved_state_has_no_transient_channel` — `history.conversations.snapshot(state)` no incluye `_channel`.
- [ ] **Step 2: Correr** → FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Correr** el archivo y la suite completa → PASS (las pruebas existentes no se tocan).
- [ ] **Step 5: Commit** `"Modo voz: respuesta corta, detalle y ¿Sigo?"`.

### Task 7: Canal desde la petición HTTP

**Files:**
- Modify: `backend/app/history/chat.py` (`chat`), `backend/app/api/routes.py` (`assistant_chat`)
- Test: `tests/test_history_chat.py` (añadir)

**Interfaces:**
- Consumes: `channel_for` (Task 4), `UnifiedAssistantService.chat(..., channel=)` (Task 6).
- Produces: `history.chat.chat(request, assistant, locks, message, session_id)` sin cambio de firma; calcula
  `channel = channel_for(getattr(request.state, "device_id", None))` y lo pasa solo si `assistant.chat` acepta el
  parámetro `channel` (`inspect.signature`; los asistentes falsos de las pruebas no lo aceptan).
  `assistant_chat`: `speech = result.pop("speech") if "speech" in result else spoken_summary(result.get("answer"))`.

- [ ] **Step 1: Pruebas que fallan** en `tests/test_history_chat.py`:
  - `test_a_device_gets_the_voice_channel` — dispositivo emparejado con dueño: la respuesta general de MockLLM
    termina en `"¿Quieres más detalle?"` y `speech` existe en el JSON.
  - `test_a_device_without_owner_also_gets_the_voice_channel` (destino legado).
  - `test_the_web_keeps_the_text_channel` — sesión web: sin oferta.
- [ ] **Step 2: Correr** → FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Correr** la suite completa de pytest y `node --test "tests/*.test.mjs"` → PASS.
- [ ] **Step 5: Commit** `"Modo voz: el canal llega desde el dispositivo"`.

### Task 8: Línea base, medición y cierre

**Files:**
- Modify: `docs/voice-assistant.md` (sección "Modo voz"), `docs/progress.md` (entrada de esta rama), `evals/README.md`

- [ ] **Step 1: Línea base (requiere permiso del dueño).** En un worktree o checkout temporal de
  `feature/device-registration` con `evals/` de esta rama copiado, correr
  `.\scripts\run-eval.ps1 --provider ollama`. Como el código viejo no acepta `channel`, `run_case` lo pasa solo si
  `chat` lo acepta (mismo `inspect.signature` de Task 7). Guardar el reporte como `evals/results/baseline-*.json`.
  *Nota de orden:* el spec pide la línea base antes del modo voz; se corre aquí sobre el código anterior, que da la
  misma medición sin bloquear las Tasks 4–7.
- [ ] **Step 2: Medición con modo voz (mismo permiso).** `.\scripts\run-eval.ps1 --provider ollama --compare
  evals/results/baseline-*.json`. Revisar fallos nuevos; ajustar `VOICE_RULES` si los conceptos en voz pasan de 3 frases
  o pierden contenido, y repetir.
- [ ] **Step 3:** Documentar en `docs/voice-assistant.md`: canal voice, ofertas, `REST_LIMIT`, y que la web no cambia.
  En `docs/progress.md`: resultados de la línea base vs. modo voz (aciertos por categoría y latencias).
- [ ] **Step 4:** Suite completa de pytest, `pip check` y `node --test` → PASS.
- [ ] **Step 5: Commit** `"Examen y modo voz: medición y documentación"` y `git push -u origin feature/examen-modo-voz`.
