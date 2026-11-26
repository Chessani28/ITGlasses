# ITGlasses progress

Current status: Phase 2 verified and complete; Phase 3 awaits approval.
The sections below preserve historical approvals and results.

## 2026-10-19 — Phase 0 reviewed and Phase 1 completed

User approved Phase 1 with Python 3.13 and a mock-only backend.

### Environment audit

- Windows 10 Pro build 19045; Intel i5-10400F, 6 cores / 12 threads.
- RAM: approximately 15.9 GiB usable, 1.3 GiB free during Phase 0.
- NVIDIA RTX 2060: 6 GiB VRAM, 3.85 GiB free; driver 576.52.
- Python 3.13.7 and 3.14.2; Git 2.46.2.
- Android Studio 2026.1 registered; SDK at D:/Android Studio SDK/SDK.
- SDK platforms 35/36; build tools 35.0.0, 35.0.1, 36.0.0.
- Java on PATH: 26.0.2. Android project Gradle JDK not inspected.
- Ollama not detected in checked locations; local API not listening.
- Project directory was empty. No Android or Unity projects were modified.

### Completed

- Created backend API, schemas, configuration, provider protocol and MockLLM.
- Implemented GET /health, GET /api/v1/status, POST /api/v1/chat.
- Added strict request validation and sanitized structured error responses.
- Mock answers explicitly state that no model or documentation is connected.
- Sources remain empty and documentation_sufficient remains false.
- Dependency injection supports future Ollama replacement without route changes.
- Development launcher binds only to 127.0.0.1:8000 and disables request logs.
- Created Python 3.13.7 virtual environment in D:/ITGlasses/.venv.
- Pip cache and installation temporary files were directed to D:/ITGlasses/.cache
  using process-scoped environment settings only.
- Added PowerShell README, dependency lock file, tests, and Git exclusions.

### Verification

- Automated suite: 20 passed in 5.39 seconds.
- Covered valid requests, boundaries, malformed JSON, missing/extra fields,
  unavailable provider, timeout, unexpected failure, provider substitution,
  whitespace normalization, liveness, HTTP errors, and loopback configuration.
- Real server startup: successful; all three endpoints passed HTTP checks.
- netstat confirmed the listener was exclusively 127.0.0.1:8000.
- Initial smoke verifier incorrectly matched the launcher PID; corrected to
  inspect the reserved port because the virtual-environment launcher delegates.
  The corrected verification passed. The verification server was stopped.
- pip check: no broken requirements found.
- FastAPI 0.149.0, Uvicorn 0.54.0, pytest 9.1.1, HTTPX 0.28.1.
- One non-failing Starlette warning: HTTPX TestClient support is deprecated
  in favor of HTTPX2. Retained the tested lock rather than adding dependencies.
- Project file sizes total approximately 49.4 MiB including environment/cache.
- Final disk snapshot: C: 5.54 GiB free; D: 723.09 GiB free.
  Space changes also reflect unrelated activity; no cleanup was performed.

### Limitations and next steps

- No real AI inference, RAG, uploads, frontend, persistence, authentication,
  phone connection, speech, or glasses integration has been implemented.
- No Ollama installation or model download was performed.
- No global environment variables were modified.
- Launcher suppresses runtime exception logs to avoid sensitive diagnostics;
  sanitized operational logging can be added in a later phase.
- Python 3.13 is approved only for this phase; check RAG package compatibility
  before introducing extraction, embeddings, or vector-store dependencies.
- Phase 2 is NOT approved. Wait for explicit approval before integrating Ollama.
- Before Phase 2: recheck free RAM/VRAM, plan runtime/model locations on D:,
  and validate a small quantized model with measured memory and latency.
- Before Wi-Fi access: implement authentication and restricted network access.

### Reproduce

```powershell
Set-Location D:\ITGlasses
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tests\smoke_server.py
.\.venv\Scripts\python.exe -m pip check
```

The smoke check requires port 8000 to be unused and starts/stops its own server.


## Phase 2 verification — 2026-10-19

Phase 2 is complete. Active inference was explicitly selected as Ollama using
qwen3:4b-instruct-2507-q4_K_M. config.toml retains mock as its development default;
scripts/start-backend.ps1 -Provider ollama selects real inference, without fallback.

The end-to-end question was: "¿Cómo se llama el proceso de borrar Windows para
realizar una instalación limpia?" FastAPI returned a final Spanish answer naming
Windows reinstallation and explaining replacement of the existing installation.
No reasoning trace or truncated response was returned. The answer also used the
imprecise synonym "limpieza de sistema"; factual terminology still needs grounding
against documentation. This test validates integration, not enterprise accuracy.

| Measurement | Observed value |
| --- | --- |
| End-to-end latency, including model load | 22.07 seconds |
| Free physical RAM before load | 7515.2 MiB |
| Minimum sampled free physical RAM | 6747.9 MiB |
| Observed system RAM availability decrease | 767.3 MiB |
| Total GPU memory used before load | 1897 MiB |
| Maximum sampled total GPU memory used | 4740 MiB |
| Observed total GPU usage increase | 2843 MiB |
| Model allocation reported by Ollama | 2.68 GiB, entirely GPU |
| Free disk after verification | C: 10.19 GiB; D: 715.72 GiB |

RAM/VRAM samples are about one second apart and include unrelated applications.
The RAM decrease is a whole-system observation, not isolated process memory or a
guaranteed peak. This is one short request, not a throughput benchmark.

Limits: 2048 context tokens, 256 output tokens, thinking=false, one generation at
a time, 30-second keep-alive, 120-second generation deadline. The test initially
stopped at 5528 MiB free RAM; it ran only after the user freed memory.

Verification: 50 automated tests passed in 1.80 seconds, including the original
20 tests. pip check reported no broken requirements. Real HTTP error checks
returned 503 model_unavailable for an absent model and 503 provider_unavailable
for a refused connection. Mocked tests cover timeouts, invalid output, remote
models, concurrency, validation and truncated-output rejection. One existing
non-failing Starlette/HTTPX deprecation warning remains.

netstat confirmed listeners exclusively at 127.0.0.1:8000 and 127.0.0.1:11434.
The test backend was stopped and model unloading requested. The pre-existing
Ollama server was left running. Sources remain empty and
documentation_sufficient=false: no RAG has been implemented.

Detailed measurements: docs/phase2-measurements.json. README contains startup,
test, configuration and storage instructions.

### Phase 2 implementation and storage record

- Official Ollama 0.40.6 standalone ZIP installed earlier in this phase at
  D:/ITGlasses/storage/runtime/ollama-0.40.6. Archive retained under .cache/downloads.
- Archive SHA-256 verified before extraction:
  e359637575302f288d76c0007c408a4be9c7376d59c4f28684d63b232a44b427.
- Models stored in D:/ITGlasses/storage/models. Pip, CUDA and temporary caches
  configured on D: using process-local settings. No global environment edits.
- Original qwen3:4b identified itself as Qwen3 4B Thinking 2507. It exhausted
  bounded generation tests without a usable final answer. A diagnostic template
  alias did not resolve that behavior; both original model and alias are retained.
- User explicitly approved the additional Instruct variant. It is the selected
  real model. No additional download or Ollama installation occurred in the final
  continuation. No existing model or project file was deleted.
- Adapter retains the provider interface and explicit MockLLM selection. Added
  local-only HTTP, metadata readiness checks, bounded generation, sanitized errors,
  remote-model rejection and no silent fallback or automatic model downloads.
- HTTPX was already installed in Phase 1; no additional Python packages needed.
- No Android, Unity, unrelated project, global environment or service changes.
- Standard Ollama identity files observed on C: total 468 bytes; no key contents
  were read. All configured large application/model/cache locations are on D:.

### Next phase — design only, not implemented

Phase 3 requires explicit approval. First review Python 3.13 compatibility before
adding extraction, local embeddings and vector storage. Later orchestration can
extract intent and identifiers from long informal questions, then combine exact
procedure-ID lookup with lexical/semantic retrieval. Ambiguous candidates should
trigger targeted clarification before an answer is generated. Grounded answers
need authorized source passages, procedure revisions, citations and explicit
abstention when evidence is insufficient. Permission enforcement, evaluation and
grounding checks are necessary; prompts alone cannot guarantee no hallucinations.
None of these enterprise/RAG capabilities were implemented in this phase.


## Phase 3 — approved and in progress, 2026-10-19

User approved the plan, listed Python dependencies and qwen3-embedding:0.6b.
Pre-installation RAM was approximately 7.48 GiB free; VRAM 4106 MiB free.
Installed pypdf 6.21.1, python-docx 1.2.0, python-multipart 0.0.32,
numpy 2.5.4 and lxml 6.1.3 in the existing D: virtual environment.
Downloaded only the approved embedding model into D:/ITGlasses/storage/models.
Component 1: four-format extraction, provenance, bounded chunks, immutable source
revisions, SQLite/FTS5 and authorization state. Tests found and fixed an insert
placeholder defect before API integration. 60 tests passed in 4.55s, including
all 50 Phase 2 tests. No original models/files or global settings were modified.


## Phase 3 completed — verification 2026-10-19 and 2026-10-20

Current status: Phase 3 implemented and verified; Phase 4 awaits explicit approval.
This entry supersedes the older status statements preserved above.

Component 2 added CPU embeddings, SQLite FTS5/vector hybrid ranking, exact-ID
ambiguity before inference, bounded conversational candidate/revision context,
conflict checks, constrained evidence selection, citations and access revalidation.
Component 3 integrated the document/knowledge REST endpoints while preserving
legacy chat and MockLLM. 79 tests passed after integration; 88 passed in 19.37s
after boundary tests, including all 50 original tests. The existing non-failing
Starlette/httpx warning remains. No test uses confidential documents.

An initial real run correctly retrieved the Windows source but abstained on
contextual and lengthy identification queries. The evidence selector now receives
explicit identification intent while information requests keep their specific
question. The subsequent real 11-case run passed, including A–F, and checked
the expected document IDs/options rather than status alone.

| Scenario | Verified outcome | Seconds |
| --- | --- | --- |
| A: informal semantic Windows description | Correct procedure and source, no execution steps | 5.371 |
| B: unique procedure 24 | Correct unique procedure and citation | 2.753 |
| C: three procedure-24 documents | Three distinct clarification options, no LLM call | 0.001 |
| D: computer/Windows follow-up | Correct candidate resolved and cited | 4.900 |
| E: absent company process | Explicit insufficient evidence | 2.920 |
| F: conflicting VPN versions | Both versions listed; no combined instructions | 0.001 |

Additional passes: long informal description (5.885s), absent company BitLocker
key despite related documentation (5.258s), revoked pending source (2.867s),
unauthorized exact ID (0.001s), and pending ambiguity (0.001s).
Observed positive source accuracy was 4/4 on A/B/D/long; the entire run was 11/11.
These results do not establish performance on an unknown enterprise corpus.

The first HTTP probe verified formats and citations but had an incorrect expected
unsupported-file status (415 instead of the implemented 400); the test expectation
was corrected. The final probe used 127.0.0.1:8001 because an existing backend
occupied 8000; that existing backend was not stopped. The probe first deferred for
insufficient RAM and succeeded after the user freed memory.

Final actual HTTP checks passed: four-format ingestion, FastAPI -> local embeddings
-> Qwen3 Instruct -> quoted evidence, source retrieval with PDF page 1, withdrawal
invalidating the source URL (404), empty input (422), unsupported file (400).
netstat verified probe and Ollama listeners on IPv4 localhost only. The owned
probe server was stopped and model unloading requested; pre-existing services
were left running.

Resource measurement: CPU embedding probe 28.403s for 16 repeated inputs;
73.078 observed CPU-seconds across Ollama runtime processes. The embedding model
reported 994,605,793 bytes resident allocation and size_vram=0. Peak sampled
combined runtime working set during embedding was 958.18 MiB. Four CPU threads
and batch size four were requested. Actual CPU time reflects workload rather
than a guessed percentage.

HTTP run baseline free RAM 6487.81 MiB, sampled minimum 5532.78 MiB (955.03 MiB
change); total GPU memory baseline 1543 MiB, sampled maximum 4412 MiB during
Instruct generation. Whole-system totals include other applications. One-second
monitor waits plus measurement overhead do not capture every instantaneous peak.
The real HTTP evidence response took 29.502s including model loading. Small-file
ingestion took MD 2.779s, TXT 2.555s, DOCX 2.940s, PDF 2.716s; these fixtures are
69 / 69 / 36,665 / 700 bytes and are not large-document throughput benchmarks.

Only the approved qwen3-embedding:0.6b was downloaded (639,150,592-byte blob).
Dependencies and caches stayed in the project on D:. requirements-lock.txt now
records the tested environment. See docs/phase3-measurements.json for exact disk
sizes, model digest, resource data, responses and raw report locations.

Operational guide: docs/knowledge.md. Repeatable opt-in probes:
tests/verify_knowledge_acceptance.py and tests/smoke_knowledge.py. Both use isolated
synthetic corpora in storage/acceptance; no existing models or files are deleted.

Known limits: local single-user authorization flag (no enterprise identity/RBAC),
one worker, bounded in-memory conversation context, no OCR, heuristic headings/
tables, one primary procedure per document, manual procedure keys for reliable
cross-document version grouping, conservative thresholds calibrated on a tiny
corpus, linear vector search, exact excerpts rather than unrestricted synthesis.
Questions above 1,800 characters request narrowing after full segmented retrieval.
Parser subprocesses have input/time bounds but no hard OS memory sandbox.
Prompt-injection defenses and verified citation IDs do not guarantee document
truth or perfect relevance. No retrieved command is executed.

Next steps require user approval: Phase 4 interface, broader labeled evaluation,
and later enterprise authentication before any network exposure. No Phase 4,
Android, voice, global environment or unrelated project changes were made.

### Final consistency check — 2026-10-20

Final full suite: 88 passed in 20.03 seconds, including all 50 Phase 2 tests.
pip check: no broken requirements found. One existing Starlette/httpx warning.
Measured project file bytes: 9,189,766,272 (approximately 8.56 GiB), including
1,942,921,587 bytes of the existing Ollama runtime and retained models/archives.
The final listener check showed Ollama on 127.0.0.1:11434; the test port 8001
was no longer listening. No service outside the owned test subprocess was stopped.
Documentation updates were preceded by byte-verified backups under
storage/documentation-backups; this progress log was extended without replacing
its historical contents.


## Phase 4 completed — 2026-10-20

Current status: Phase 4 implemented and verified; Phase 5 requires explicit approval.
This entry supersedes earlier phase status statements without removing history.

Added a dark responsive local web interface with separate general/knowledge modes,
loading/errors/latencies, temporary context references, ambiguity options, current
source inspection, upload and revision forms, document access/reindex/withdrawal,
and actual provider/retrieval status. No model/retrieval algorithms were changed.
Only FastAPI frontend hosting/security headers and local static assets were added.
No package installation, model download, cloud service or global environment edits.

Tests: 92 Python tests passed in 25.47s (all 88 Phase 1–3 tests preserved), plus
11 JavaScript tests using node:test. After fixing the selection payload to preserve
identification intent and avoid title-ID re-parsing, the four affected integration
tests passed again in 3.08s. pip check found no broken requirements. The existing
Starlette/httpx warning remains non-failing.

Browser verification on an isolated synthetic corpus exercised general chat,
knowledge answers, file chooser upload, authorization/revocation, reindex revision
2, logical withdrawal, old source rejection, two-candidate ambiguity and explicit
name-only selection. Literal HTML in a source rendered as text without injected
image elements. Desktop and 390/320-pixel mobile layouts were inspected; narrow
chat and document views had no horizontal overflow. UI operations were verified
against the real FastAPI routes using MockLLM and synthetic test adapters; these
observations are not a fresh Qwen inference benchmark.

The temporary UI test server was stopped; its files remain in the isolated
storage/phase4-ui-verification directory. The normal backend was already running
on localhost afterward and was preserved. Ollama was not listening at final
service checks, so it was started using the existing D: launcher with cloud off,
without reinstalling or downloading models. Final free RAM was approximately
4029 MiB, below the prior 6 GiB benchmark headroom, so no real-model inference
was launched in this phase. Use historical Phase 3 results only as such.

Documentation: docs/interface.md and README.md. Existing documentation and changed
files were backed up under storage/phase4-backups. Progress was appended only.
Limits: no general-chat memory, no persistence on reload, no streaming, safe plain
text rather than rendered Markdown, no hardware metrics, single-user localhost
scope and existing Phase 3 constraints. No Android/voice/Phase 5 changes.


## Phase 5 implementation checkpoint — 2026-10-20

Status: voice implementation and deterministic validation complete; model download
approval and real speech/microphone acceptance are pending. Phase 5 is NOT yet
accepted. No Phase 6 implementation was started.

Audited existing routes, provider contracts, context behavior, storage and web UI.
Added CPU-only Vosk/Piper worker adapters, bounded WAV validation, model-presence
status, sanitized errors, one speech worker at a time, deadlines/disconnection
cleanup, and source revalidation before and after TTS. No retrieval or LLM logic
was changed. Added AudioWorklet capture, silence segmentation, explicit experimental
wake-prefix matching, visible microphone/state/transcript indicators, playback,
stop/cancellation, configurable follow-up windows and voice preferences. Existing
chat rendering, knowledge sessions, ambiguity choices and source inspection are reused.

Final deterministic run: 114 Python tests passed in 29.13s (all 92 prior tests
preserved); 24 JavaScript tests passed. pip check found no broken requirements.
The existing Starlette/httpx deprecation warning is non-failing. Tests cover invalid
audio, model absence, concurrency, cancellation, late microphone grants, follow-ups,
silence segmentation, revoked sources, and withdrawal during speech generation.
The real browser displayed the new panel and correctly reported missing models.
No microphone capture, real speech inference or audio quality has been verified.

Audit baseline: RAM free 6415.55 MiB; GPU used 1655 MiB/free 4299 MiB.
C: free 9887039488 bytes; D: free 767628742656 bytes. These are baseline readings,
not model resource consumption. No packages or speech models were downloaded or
installed. Optional requirements pin vosk 0.3.45 and piper-tts 1.8.0; Windows wheels
exist, but runtime compatibility awaits real testing. Proposed models: Vosk small
Spanish 0.42 (~39 MB) and Piper es_ES-sharvard-medium (~64 MB), entirely on D:.

Backups of changed files are in storage/phase5-backups. Runtime audio is isolated
in .cache/voice and only request-owned temporary audio is removed. Existing models,
documents, databases, Android/Unity projects and global environment variables were
not modified. The localhost port was free at verification, so the normal backend
was started with python -m backend.run; no existing backend process was terminated.

Documentation: README.md and docs/voice-assistant.md. Prepared tests/smoke_voice.py
for an opt-in synthetic speech real-model probe; it does not auto-download anything.
Remaining: receive model approval, install on D:, test actual STT/TTS, measure model
resources and latency, verify physical microphone/audio playback, and evaluate wake
phrase/technical Spanish accuracy. Limitations: half-duplex; general chat stateless;
prototype wake matching; no real glasses integration. Published DAT capabilities
are documented separately from unverified device/firmware/audio-routing assumptions.

## Users and roles — branch feature/users, 2026-11-23

Seven stages on top of the tag voz-rapida-v3, each committed after its tests:

1. Device database schema v2 (users, web_sessions, audit_log, device owner) with a verified backup
   before any migration and `scripts/restore-pairing-backup.ps1`.
2. Accounts with scrypt, audit log, local scripts `create-superadmin.ps1` and `reset-access.ps1`;
   2b: configurable password minimum (`ITGLASSES_PASSWORD_MIN_LENGTH`, default 5).
3. Server-side sessions, login/logout/me/password change, lockout, Origin and CSRF checks.
4. Permissions by role, `ITGLASSES_LOOPBACK_MODE` and `ITGLASSES_BEARER_SCOPE`; S1 closed.
5. User administration, device owners, "my devices", read-only audit log; device and manual actions audited.
6. Login screen, user menu and administration pages; `LoopbackMode` defaults to `chat`.
7. Documentation (`docs/users.md`), end-to-end acceptance test, footer text.

Verified by the user on the real system: migration of the real database with backup, superadmin created
with its 4 approved devices assigned, phone v3 (DPoP) with glasses and locked phone after stages 2, 4, 5
and 6, the web flows of stage 6 (login, roles, users, temporary password with forced change, device owners,
audit). Rollback without code: `LoopbackMode = 'legacy'`, `BearerScope = 'full'`.

Next: chat history per user and device (then `LoopbackMode = 'off'`); afterwards, the web over HTTPS in
Caddy for other PCs, raising the password minimum first.

## Chat history — branch feature/chat-history, 2026-11-24

Six stages on top of the tag usuarios-v1, each committed after its tests:

1. Separate database `storage/history/history.sqlite3`, verified backups before migrations, daily backup set
   (7 kept), `scripts/backup-data.ps1` and restore with `-History`.
2. The assistant chat saved per user (web) and per device (phone and glasses share one conversation, 30-min
   gap), the assistant state restored after a restart; never breaks the chat if the history fails.
3. Context as real turns within `num_ctx` (E4); general follow-ups no longer fall into the manuals route;
   one rule for the general-answer notice. Public search: ddgs engines in a fixed order with a time budget,
   short cache of current facts, diagnostics in backend.log without the query.
4. Conversation endpoints, admin metadata, retention (90 days) and purge of deleted ones (7 days of grace),
   audit without content.
5. Web history panel, "new conversation" per device, admin column, Vosk-instead-of-Whisper notice, voice
   panel texts.
6. `LoopbackMode` defaults to `off`; guide `docs/history.md`; end-to-end acceptance test.

Verified by the user on the real system: web history, filters, open/continue and the Z Flip6 conversations;
public search 26H2 with official source. Pending: history panel horizontal scroll and cut titles; the app
showing the Vosk reason (Android stage); glasses with the locked phone.

## Examen del cerebro y modo voz — rama feature/examen-modo-voz, 2026-11-25

1. Examen del cerebro (`evals/`, `scripts/run-eval.ps1`): 102 casos en JSON (conceptos, internet, social,
   conversaciones, voz y manuales; los de manuales en `evals/cases/local/`, no versionados), revisiones automáticas y
   reporte con aciertos por categoría, latencias y cambios contra la corrida anterior. Corre en proceso con una copia
   del índice; nunca escribe en `storage/` ni usa el puerto 8000.
2. Modo voz para celular y lentes (`backend/app/assistant/voice_mode.py`): respuesta general de 1–2 frases con
   "¿Quieres más detalle?", detalle que amplía lo escuchado, "¿Sigo?" en vez de "El resto está en el chat", procedimientos
   sin cambios, web sin cambios. Detalle en `docs/voice-assistant.md`.

Medición con Ollama real (qwen3:4b-instruct, backend activo a la vez):

| Categoría | Línea base | Modo voz |
|---|---|---|
| Total | 68/102 | 72/102 (voz re-medida después: 12/12) |
| voz | 6/12 (p90 9.9 s) | 12/12 (mediana 1.1 s, p90 3.7 s) |
| conceptos | 23/25 | 24/25 |
| conversaciones | 13/15 | 13/15 |
| internet | 12/15 | 12/15 |
| manuales | 9/30 | 9/30 |
| social | 5/5 | 5/5 |

Hallazgos para lo siguiente: (a) **manuales 9/30**: preguntas en español sobre el manual en inglés (Training Setup
Guideline) no recuperan evidencia, caen a conocimiento general y el modelo inventa (16 de 21 fallos); 3 fallos más eran
un error del examen ya corregido (`source_document`). (b) La búsqueda web solo se activa con palabras como "versión" o
"última": "¿Windows 10 tiene soporte?", "precio de un SSD" y "¿Teams tiene una caída?" no buscan.

## Manuales en inglés con preguntas en español — rama feature/manuales-bilingues, 2026-11-25

Causa raíz encontrada con el examen: el Ollama en uso lo había arrancado la app de escritorio de Ollama con su carpeta
de modelos por defecto, sin `qwen3-embedding:0.6b`; la búsqueda funcionaba solo por palabras. Se reinició con
`start-ollama.ps1`. Luego, para preguntas en español sobre manuales en inglés: traducción de la consulta y búsqueda
con ambas, 4 pedazos al selector, filtro intermedio con las palabras del usuario, y sin intercambio de modelos
(`OLLAMA_MAX_LOADED_MODELS=2`, embeddings en CPU sin descargarse). Detalle en `docs/knowledge.md`.

| Examen (Ollama real) | Total | Manuales | Conceptos | Voz | Mediana manuales |
|---|---|---|---|---|---|
| Sin embeddings (Ollama de la app) | 72/102* | 9/30 | 24/25 | 12/12* | 1.6 s |
| Con embeddings, sin traducción | 80/102 | 16/30 | 23/25 | 12/12 | 6.0 s |
| Con traducción y sin intercambio | 88/102 | 22/30 | 24/25 | 12/12 | 1.9 s |

\* voz re-medida después del arreglo del detalle. Faltan: 8 de manuales (respuestas de información que salen como
extracto literal en inglés; "etiquetas" queda bajo el filtro), y la web solo busca con "versión"/"última".
Para que no vuelva a pasar: quitar el inicio automático de la app de Ollama o fijar `OLLAMA_MODELS`.

## Respuestas de manuales en español y búsqueda — rama feature/respuestas-manuales, 2026-11-25

- Respuestas de información con frases del manual elegidas por número (`pick_sentences`) y traducidas con los
  candados de `_translate`, con título y página; sin selección, extracto limpio (sin portadas, índices, páginas sueltas
  ni viñetas). Candado nuevo: los nombres en pantalla (varias palabras con mayúscula, texto entre comillas) se conservan.
- Selector de evidencia con la pregunta también en inglés, sin portadas/índices entre sus 4 pedazos, y con un reintento
  ante JSON inválido (el segundo fallo es "sin evidencia", no "No se pudo consultar").
- Una pregunta que nombra un término de los manuales (nombres y siglas: Dominos, SUMMIT, CPU) los consulta primero.
- Intención: "qué es lo primero…", "que esté…" y "cómo se llama la carpeta" ya no son preguntas de identificación.

Examen: total 88/102, manuales 22/30 (estable entre corridas). Lo que falta en manuales depende de la selección del
modelo de 4B (elige la página vecina) y del filtro de relevancia ("etiquetas" 0.42, "secpol" 0.52).

## Arranque único, secretos y mejoras sin lentes — 2026-11-25

- `start-all.ps1` / `stop-all.ps1` (rama `feature/arranque-unico`): Ollama + backend + Caddy con un comando, aviso de
  IP cambiada y de RAM insuficiente para Whisper.
- Secretos de los manuales ocultos como `[OCULTO]` al subir y al leer (`feature/secretos-manuales`).
- `feature/mejoras-sin-lentes`: búsqueda web para hechos que cambian (hoy, precio, caídas, soporte, noticias), pasos de
  procedimientos en español en la voz (una llamada, con candados), limpieza de código muerto.
- Android `feature/lint-meta`: `android:exported` del flavor meta; 292 pruebas por flavor pasando.

Examen final: **92/102** (internet 15/15, manuales 23/30, voz 12/12, conceptos 24/25). Costo: la mediana de manuales
subió a ~4.9 s porque el modelo de embeddings se descarga tras cada búsqueda para dejar RAM a Whisper.

## Velocidad, seguimientos y monitoreo — rama feature/velocidad-y-monitoreo, 2026-11-25

- Manuales más rápidos: los embeddings se quedan cargados con ≥3.5 GB libres; si Whisper no cabe, la voz los libera
  primero. Mediana de manuales 4.9 s → 2.8 s con la misma precisión.
- Seguimientos: procedimientos sin título conservan sus pasos; listas bajo encabezados distintos ya no se pisan
  (manual de VPN); se elige la lista que coincide claramente. Conversaciones 13/15 → 15/15.
- Monitoreo para administradores (Estado del sistema → "Monitoreo (última hora)"): tiempos por etapa, motor de voz
  (Whisper/Vosk) y por qué falló Whisper, último error y RAM libre. Solo códigos y tiempos, en memoria
  (`GET /api/v1/admin/metrics`).

## RAM del modelo de chat — rama feature/ram-ollama, 2026-11-25

Causa de que la voz cayera a Vosk: la caché de prompts en RAM de `llama-server` (default hasta 8 GB) crecía ~40 MB por
pregunta y no se liberaba (4.5 → 5 GB en 12 preguntas; privada 9.7 GB tras un día). `start-ollama.ps1` la desactiva
(`LLAMA_ARG_CACHE_RAM=0`): el proceso queda fijo en ~0.7 GB tras 80+ preguntas, RAM libre 7.8–8.8 GB. Examen sin
cambios en aciertos (voz 12/12, conceptos 24/25, manuales 23/30) y conceptos más rápidos (mediana 2.3 s).
Para aplicarlo a un Ollama ya corriendo: `stop-all.ps1` y `start-all.ps1`.

## Pedazos más finos en los manuales — experimento, 2026-11-25

Copias del índice reindexadas con pedazos de 450 y 300 caracteres (hoy 900, casi una página por pedazo): manuales
23/30 en los tres tamaños; con 300 un caso empeora (Dominos responde un fragmento). **No se reindexó el índice real.**
El límite que queda es la selección del modelo de 4B (elige la página vecina). El tamaño queda configurable
(`ITGLASSES_CHUNK_CHARS`, 900 por defecto) para manuales futuros. Dos casos del examen fallaban por palabras clave
("nombre de host", "no se puede determinar") y se ampliaron (casos locales).

## Medición de un turno de voz y Whisper siempre listo — rama feature/whisper-base, 2026-11-25

Turno medido sin lentes (registro real de 26 transcripciones + Piper directo + examen):

| Etapa | Cargado | En frío |
|---|---|---|
| Voz a texto (Whisper) | mediana 1.1 s | 15–18 s (p90 16 s) |
| Asistente | ~1.8 s | ~2–3 s |
| Texto a voz (Piper) | ~0.4 s por 6–8 s de audio | ~7 s (se precarga al arrancar) |

Los fríos de Whisper venían de su umbral para quedarse cargado en CPU (8 GB libres), que casi nunca se cumplía: cada
pregunta lo recargaba. Con la RAM estable se baja a 4 GB (`WHISPER_KEEP_RAM_DEFAULT`, `WhisperKeepRamGiB`); el monitor
lo sigue descargando bajo 1.5 GB. En GPU (turbo) siempre se queda. El streaming ahorraría ~1 s de un turno caliente de
~3.3 s.

### Medición de voz a texto con grabaciones reales (work/voice-human-eval, 20 frases, 2026-11-26)

| Motor | Error por palabra (WER) | Mediana cargado | Cuándo se usa |
|---|---|---|---|
| Whisper large-v3-turbo (GPU) | **16.4%** | **0.44 s** | normalmente (frío: ~18 s, se precarga) |
| Whisper Small (CPU int8) | 27.2% | 1.9 s | si la GPU no tiene VRAM libre |
| Vosk | 41.0% | 1.1 s | si Small no cabe en RAM (en el celular) |
| Whisper base (CPU) | 45.3% | 0.67 s | probado y **descartado**: no mejora a Vosk |

El respaldo con Whisper base se revirtió (commit "Revert …Whisper base…"). Lo que más pesa en la calidad es que turbo se
quede en la GPU: apps que usan VRAM (Chrome, reproductores, overlay de NVIDIA) lo empujan a Small en CPU. Errores
típicos aun con turbo: nombres en inglés ("Outlook" → "outlaw", "ITGlasses" → "y qué clases"); el vocabulario de
pistas de Whisper (`vocabulary.py`) es donde atacarlos.
