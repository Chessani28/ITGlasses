# Phase 4: local web testing interface

The interface is available at http://127.0.0.1:8000/ and is served by the existing
FastAPI application. It uses local HTML, CSS and vanilla JavaScript modules.
There is no frontend build, npm install, CDN, cloud API or new Python dependency.

## Start on Windows PowerShell

If Ollama is not already running, start it in one terminal:

```powershell
Set-Location D:\ITGlasses
.\scripts\start-ollama.ps1
```

Start the backend in another terminal, unless it is already running:

```powershell
Set-Location D:\ITGlasses
.\scripts\start-backend.ps1 -Provider ollama
```

Open http://127.0.0.1:8000/ in a modern browser. Keep the terminal open; Ctrl+C
stops the server started in that terminal. Do not start another process on an
occupied port. Restart a backend that was started before Phase 4 was installed.
For explicit mock general chat, use `-Provider mock`. This does not replace the
normal knowledge pipeline with fake embeddings. No model is downloaded on demand.

## Using the workspace

- **Conversaciones / Chat general:** sends only the current question to
  `/api/v1/chat`. Previous messages are visible but are not sent as invented
  conversation context. The existing backend does not support general-chat memory.
- **Conversaciones / Conocimiento:** uses `/api/v1/knowledge/chat` and carries the
  returned conversation ID. Clarification options send the exact selected UUID,
  preserving identification intent without accidentally re-parsing IDs in titles.
  Earlier options become disabled after a subsequent reply.
- **Nueva conversación:** resets the selected mode's local display and conversation
  reference. Reloading clears both displays. No localStorage, sessionStorage or
  server-side chat archive has been added. The knowledge backend retains its
  existing bounded temporary sessions; expired-session errors start fresh on the
  next question rather than silently retrying an operation.
- **Citations:** click a source to fetch its current authorized excerpt, filename,
  version, revision, available page/section and SHA-256. Withdrawn, unauthorized or
  superseded sources display an error. Old answers already displayed are historical;
  reopening a citation always checks the backend again.
- **Documentos:** select PDF, DOCX, UTF-8 TXT or Markdown, enter title/version/scope
  and optional procedure ID/key, then upload. Explicitly check authorization if
  the document should be eligible for answers. Uploaded files default to unauthorized.
- **Nueva versión:** selects an existing document for replacement through PUT.
  Choose the new file and adjust its version; original revisions are preserved.
- **Reindexar / Autorizar / Revocar / Retirar:** use the existing backend endpoints.
  Withdrawal requires a local confirmation and is logical; the original file stays
  on disk. Withdrawn records remain visible and their operations are disabled.
- **Estado del sistema:** reports actual health, active general-chat provider/model,
  embedding availability and eligible document count. With mock selected, the UI
  explicitly does not claim that the Ollama answer model was checked. Status is
  refreshed at startup, on opening the panel, after document operations, or manually.

The general-chat and knowledge panels are separate. Identification requests do
not expose execution steps merely because the user clicks an ambiguity option.
See `knowledge.md` for metadata semantics and the unchanged backend limitations.

## Safety and limits

Only the frontend directory is mounted publicly. Project documents, indexes,
configuration files and models are not static assets. Both services keep their
existing localhost binding. UI content uses DOM text nodes/textContent; model or
document HTML cannot become executable markup. A same-origin Content Security
Policy blocks external scripts, inline scripts and embedding the page in frames.
No command execution control, authentication framework, RBAC or OAuth was added.

File selection validates supported extensions, nonempty contents and the 10 MiB
limit before sending. The backend still validates the actual uploaded content.
Document indexing uses an indeterminate loading state because the backend does
not report progress percentages or asynchronous job status. Request durations are
measured in the browser and include network/backend time; no hardware values are
invented. Slow operations can outlive a client timeout; check the list/status before
repeating an upload. There is no token streaming or Markdown/HTML rendering.

General questions are limited to 4,000 characters; knowledge questions to 16,000,
with the existing backend clarification threshold for long inputs. All existing
Phase 3 relevance, OCR, access and conversation limits still apply.

## Tests and verification

```powershell
Set-Location D:\ITGlasses
$env:TEMP = 'D:\ITGlasses\.cache\tmp'
$env:TMP = $env:TEMP
.\.venv\Scripts\python.exe -m pytest -q -o cache_dir=.cache/pytest
node --test tests\frontend.test.mjs
.\.venv\Scripts\python.exe -m pip check
```

Node is needed only for the dependency-free frontend contract tests, not to run
the app. The existing Node installation was used; no npm packages were installed.
92 Python tests passed (all previous 88 plus four interface/integration tests).
11 JavaScript tests passed. After adjusting the explicit-choice request, the four
affected Python integration tests passed again. pip check found no broken packages.
The existing Starlette/httpx deprecation warning remains non-failing.

Actual browser verification used an isolated FastAPI application with MockLLM and
deterministic synthetic embedding/evidence adapters, preserving the normal corpus.
The following were observed through the interface:

- Independent general and knowledge modes; general-mode history was not sent.
- File chooser upload, index success, title/version/ID metadata and authorization.
- Knowledge answer, exact source passage and provenance in a modal.
- Authorization revocation/restoration, reindexing to revision 2, and withdrawal.
- Old citation rejection with source_unavailable, without retaining the old excerpt.
- Two exact-ID candidates, explicit selection, and a name-only answer after selection.
- HTML-shaped source content displayed literally; zero injected image elements.
- System panel correctly identified mock and synthetic embedding adapters.
- Desktop 1280x900 and mobile 390x844 / 320x740 layouts. Chat and document views
  did not horizontally overflow at the checked narrow sizes.

Test corpus: `storage/phase4-ui-verification`, separate from the normal document
store. The test server was stopped. The normal backend/provider status was checked
afterward. Phase 4 does not claim a new real-model inference benchmark: available
RAM was below the prior 6 GiB test headroom during final checks. Phase 3's measured
real-model results remain historical evidence, not a fresh UI inference result.

## Files and scope

`frontend/index.html`, `styles.css`, `app.js`, `core.mjs`, `favicon.svg` provide the
interface. `backend/app/frontend.py` mounts assets and adds security headers;
`main.py` calls that integration. Existing providers, embeddings, extraction and
retrieval logic were not changed. Backup copies are in `storage/phase4-backups`.

Phase 5, Android, wearable connectivity and voice remain unimplemented.
