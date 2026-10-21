# Phase 5 voice implementation — acceptance pending

Local CPU speech adapters and a web voice panel have been added. Speech model
downloads are pending explicit approval; real STT/TTS and microphone acceptance
have not been performed. The existing text interface remains operational.

See [Voice assistant](docs/voice-assistant.md) for architecture, optional installation,
storage paths, PowerShell startup/testing commands, licenses and limitations.

Added endpoints: `GET /api/v1/voice/status`, `POST /api/v1/voice/transcribe`
(mono PCM16 WAV), and `POST /api/v1/voice/synthesize` (JSON to WAV).
Start the backend with `.\.venv\Scripts\python.exe -m backend.run` from
`D:\ITGlasses`, then open http://127.0.0.1:8000/.

Verification: **114 Python tests + 24 JavaScript tests passed**; no broken pip
requirements. These are deterministic tests, not real speech inference results.
General chat remains stateless; Knowledge chat preserves the existing temporary
conversation context. Wake activation is explicitly experimental. No Phase 6 work.

---

# ITGlasses — Local web workspace

Phase 4 provides a responsive dark web interface at **http://127.0.0.1:8000/**.
Use it for general chat, grounded knowledge questions, clarification choices,
source inspection, document upload/versioning/reindexing/authorization/withdrawal,
and actual service status. HTML, CSS and vanilla JavaScript are served by FastAPI;
no frontend build or additional dependencies are required.

Start Ollama if needed with `scripts/start-ollama.ps1`, then the backend with
`scripts/start-backend.ps1 -Provider ollama`, from `D:\ITGlasses` in separate
PowerShell terminals. Open the URL above. Avoid starting duplicate services.
Full startup, usage, verification and limitations: [Web interface guide](docs/interface.md).

Verification: 92 Python tests and 11 JavaScript tests passed. Browser checks used
an isolated synthetic corpus and mock model adapters; both chat modes, upload,
citations, access updates, reindexing, withdrawal and ambiguity choices were tested.
The actual-model benchmark below belongs to Phase 3; it was not repeated in Phase 4.
General chat is stateless; knowledge carries only the existing temporary backend
context. Displayed history is cleared on reload. No Phase 5 work has started.

## Historical Phase 3 implementation and verification


Phase 3 adds local document ingestion and grounded knowledge retrieval using
SQLite/FTS5 and CPU qwen3-embedding:0.6b embeddings. Ollama Qwen3 Instruct selects
existing evidence; responses identify a procedure or quote documented passages.
88 automated tests pass, including all original 50 Phase 2 tests.

Use **POST /api/v1/knowledge/chat** for documented company information.
The original **POST /api/v1/chat** remains general/mock chat with empty sources
and documentation_sufficient=false. It is not a company-policy endpoint.
MockLLM remains available explicitly; there is no silent fallback.

Start instructions below are unchanged. See [Knowledge API guide](docs/knowledge.md)
for PowerShell upload/query/update/withdraw/reindex commands, metadata, limits,
and security boundaries. No frontend or Phase 4 implementation is included.

## Phase 3 verified results — 2026-10-19 and 2026-10-20

All six acceptance scenarios A–F passed with real local models. The extended
11-case run also covered a long informal query, a missing company-specific fact
despite related Windows evidence, a revoked pending conversation, and unauthorized
documents. Expected-source checks passed for all four positive cases; both
ambiguity/conflict cases returned the expected candidate sets. This is a small
synthetic acceptance set, not an enterprise accuracy claim.

- Real A/D/long-answer latency: 5.371 / 4.900 / 5.885 seconds.
- Separate HTTP inference including model loading: 29.502 seconds.
- HTTP ingestion of small MD/TXT/DOCX/PDF fixtures: 2.555–2.940 seconds/file.
- CPU embedding probe: 16 repeated synthetic inputs in 28.403 seconds,
  73.078 observed process CPU-seconds; peak combined Ollama runtime working set
  958.18 MiB during the probe. Ollama reported 994,605,793 model bytes and
  **zero VRAM bytes** allocated to embeddings.
- HTTP run: free physical RAM 6487.81 -> minimum 5532.78 MiB;
  total GPU memory used 1543 -> peak 4412 MiB with Instruct inference.
  These are sampled system totals, affected by other applications.
- Actual localhost HTTP checked all four formats, a PDF page citation, withdrawn
  source rejection (404), input validation (422), and unsupported format (400).

The approved embedding blob uses 639,150,592 bytes on D:. Models, dependencies,
indexes, originals and large caches remain on D:. No additional models, global
settings, Android/Unity changes or cleanup were introduced.

Results and raw report locations: docs/phase3-measurements.json.
Single-user local authorization is implemented; enterprise identity/RBAC,
OCR and large-corpus evaluation remain future work. Exact source excerpts are
verifiable but do not guarantee source correctness or perfect relevance.

## Historical Phase 2 verification — 2026-10-19

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

## Start on Windows PowerShell

Open two terminals. The scripts set only process-scoped variables and restore them
when they exit. No activation, global PATH changes, or system-wide policy changes
are needed. If local policy blocks a script, review it before using a process-only
execution policy; do not change machine policy.

Terminal 1 — Ollama:

```powershell
Set-Location D:\ITGlasses
.\scripts\check-resources.ps1
.\scripts\start-ollama.ps1
```

Terminal 2 — backend using Ollama:

```powershell
Set-Location D:\ITGlasses
.\scripts\start-backend.ps1 -Provider ollama
```

For development without a model, select mock explicitly instead:

```powershell
.\scripts\start-backend.ps1 -Provider mock
```

Stop each server with Ctrl+C in its terminal. No startup task or Windows service
was installed. FastAPI listens only at `127.0.0.1:8000`; Ollama listens only at
`127.0.0.1:11434`. Do not expose either unauthenticated service to Wi-Fi or the
internet. The low-memory settings allow one model and one generation at a time.

`config.toml` configures model, loopback URL, context, output length and timeouts.
Its development default is `mock`. The startup script explicitly selects `ollama`
by default through a temporary `ITGLASSES_PROVIDER` override. An Ollama failure
never switches to mock. Invalid provider names fail startup.

Initial limits: 2048 context tokens, 256 output tokens, thinking disabled,
120-second generation deadline, 5-second health request timeout, 30-second model
keep-alive. Health checks read metadata without loading the model. Check resources
before first inference; initially target at least 6 GiB free RAM and 3500 MiB free
VRAM. Model fit depends on current workload, context and runtime version.

## Test

```powershell
Set-Location D:\ITGlasses
$env:TEMP = 'D:\ITGlasses\.cache\tmp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = 'D:\ITGlasses\.cache\pip'
.\.venv\Scripts\python.exe -m pytest -q -o cache_dir=.cache/pytest
.\.venv\Scripts\python.exe -m pip check
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/api/v1/status
$body = @{ message = 'Explain DNS briefly.' } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/api/v1/chat -Method Post -ContentType 'application/json' -Body $body
```

Automated tests use mocked HTTP; no model or Ollama server is needed. The original
mock startup check is `python tests\smoke_server.py` using the virtual environment.
For real inference run `.\.venv\Scripts\python.exe tests\smoke_ollama.py` with Ollama
running, the model installed, and port 8000 unused. It checks resources, starts the
backend, samples RAM/VRAM, uses a synthetic question, records measurements in
`docs/phase2-measurements.json`, stops the backend and requests model unload.

## API and errors

- `GET /health`: application liveness, independent of the provider.
- `GET /api/v1/status`: selected provider/model and readiness. `unavailable` means
  the runtime/model is absent, invalid or unreachable. It does not load weights.
- `POST /api/v1/chat`: JSON with only `message`, trimmed to 1–4000 characters.
  Returns answer, provider, model, sources and documentation_sufficient.

Errors use `{"error":{"code":"...","message":"..."}}`:
422 validation_error; 503 model_unavailable or provider_unavailable;
504 provider_timeout; 502 provider_response_error or provider_output_limit; 500 internal_error. Truncated generations are rejected.
No upstream response bodies, prompts, or exception diagnostics are returned.
Interactive docs: http://127.0.0.1:8000/docs.

## Storage and installation

- Python 3.13.7 environment: `D:\ITGlasses\.venv`.
- Official standalone runtime: `D:\ITGlasses\storage\runtime\ollama-0.40.6`.
- Models: `D:\ITGlasses\storage\models` via `OLLAMA_MODELS`.
- Retained installer archive: `D:\ITGlasses\.cache\downloads`.
- Temporary files: `D:\ITGlasses\.cache\tmp`.
- CUDA cache: `D:\ITGlasses\.cache\cuda`.
- Pip cache: `D:\ITGlasses\.cache\pip`.

The official standalone ZIP was downloaded directly to D: and SHA-256 verified:
`e359637575302f288d76c0007c408a4be9c7376d59c4f28684d63b232a44b427`.
Archive size: 1,471,164,488 bytes. No installer, registry/PATH edits, services,
automatic updates, or cleanup were used. The retained ZIP intentionally remains.
Ollama created its standard identity files in `%USERPROFILE%\.ollama` (468 bytes
total); their contents are private and are not tracked or copied. Windows/driver
activity and virtual memory can still affect C: independently of these paths.

The launcher disables cloud features, request-body debug logs, interactive
history and startup model pruning. Normal operational logs go to the console;
the initial synthetic verification log is in `.cache/ollama-phase2.log`.
No confidential documentation was used.

To recreate only Python dependencies on D: (do not overwrite an active environment):

```powershell
Set-Location D:\ITGlasses
New-Item -ItemType Directory -Force .cache\pip, .cache\tmp | Out-Null
$env:PIP_CACHE_DIR = 'D:\ITGlasses\.cache\pip'
$env:TEMP = 'D:\ITGlasses\.cache\tmp'
$env:TMP = $env:TEMP
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
```

HTTPX was already installed for Phase 1 tests and is now also a runtime dependency.
No additional Python packages were installed for Phase 2.

## Architecture and boundaries

API schemas, configuration, providers and application assembly are separate.
`LLMProvider` defines availability and generation with typed errors.
`create_app(provider=...)` allows dependency injection. Mock and Ollama share
the API contract. Ollama uses local HTTP only, ignores proxy environment settings,
does not follow redirects, rejects remote model metadata, serializes generation,
and never downloads missing models automatically.

The approved Phase 3 dependencies were installed and tested on Python 3.13.7.
Private data, models, caches and environments are excluded from Git.
Phase 4 requires explicit approval. See `docs/progress.md` for results.

The original qwen3:4b download identified itself as a Thinking model and did not complete the bounded DNS test. The user separately approved the Instruct variant. The original model and an unused template-only diagnostic alias (itglasses-qwen3:4b) are retained without cleanup. Only the Instruct variant is selected by config.toml. The diagnostic Modelfile is not needed for startup.

Official references:
- https://docs.ollama.com/windows
- https://docs.ollama.com/faq
- https://docs.ollama.com/api/chat
- https://github.com/ollama/ollama/releases/tag/v0.40.6
