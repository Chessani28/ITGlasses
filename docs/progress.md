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
