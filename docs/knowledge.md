# Phase 3: local knowledge retrieval

The knowledge API uses authorized current document revisions as evidence. It is
separate from the unchanged Phase 2 `/api/v1/chat` endpoint, whose responses are
general model knowledge and are not verified company procedures.

## Run and query

Use the existing PowerShell launchers from `D:\ITGlasses`. Do not start a second
Ollama process if it is already running.

```powershell
Set-Location D:\ITGlasses
.\scripts\check-resources.ps1
.\scripts\start-ollama.ps1
```

In another terminal:

```powershell
Set-Location D:\ITGlasses
.\scripts\start-backend.ps1 -Provider ollama
```

The backend binds exclusively to `127.0.0.1:8000`; Ollama uses
`127.0.0.1:11434`. Use one backend worker. The knowledge endpoint always uses
the installed local embedding model and evidence selector, even when the legacy
chat provider is explicitly set to mock. Automated tests inject fake components
and never download models.

To index the supplied synthetic example once, from a third PowerShell terminal:

```powershell
Set-Location D:\ITGlasses
$upload = curl.exe --silent --show-error --fail `
  -F "file=@D:\ITGlasses\tests\fixtures\knowledge\windows.md" `
  -F "metadata=<D:\ITGlasses\tests\fixtures\knowledge\windows.metadata.json" `
  http://127.0.0.1:8000/api/v1/documents
$doc = $upload | ConvertFrom-Json
$doc.id

$payload = @{
  message = 'Tengo Windows y quiero dejarlo desde cero con una USB. No recuerdo como se llama.'
} | ConvertTo-Json
$answer = Invoke-RestMethod http://127.0.0.1:8000/api/v1/knowledge/chat `
  -Method Post -ContentType 'application/json; charset=utf-8' `
  -Body ([Text.Encoding]::UTF8.GetBytes($payload))
$answer | ConvertTo-Json -Depth 8

Invoke-RestMethod http://127.0.0.1:8000/api/v1/documents
Invoke-RestMethod http://127.0.0.1:8000/api/v1/knowledge/status
```

The response status is `answered`, `clarification_required`, `conflict`, or
`insufficient_evidence`. Similarity scores are ranking signals, not probabilities.
When clarification is requested, preserve the returned conversation ID:

```powershell
$payload = @{
  message = 'El de las computadoras, donde se borra Windows.'
  conversation_id = $answer.conversation_id
} | ConvertTo-Json
$answer = Invoke-RestMethod http://127.0.0.1:8000/api/v1/knowledge/chat `
  -Method Post -ContentType 'application/json; charset=utf-8' `
  -Body ([Text.Encoding]::UTF8.GetBytes($payload))
```

Alternatively send `choice_document_id` with an ID from `options` and the same
conversation ID. An explicit choice cannot restore revoked access or a superseded
revision. Inspect a citation's exact passage:

```powershell
if ($answer.sources.Count -gt 0) {
  Invoke-RestMethod ("http://127.0.0.1:8000" + $answer.sources[0].url)
}
```

Identification requests return a title and citation without execution steps.
Documented information and steps are returned as exact selected excerpts.
The LLM selects evidence through constrained JSON; its free-form prose is not
returned as company policy. A request for steps without documented steps is
answered with insufficient evidence.

## Metadata and document lifecycle

Metadata is a JSON form field. `title` is required; choose an informative title.
`version` defaults to `"1"`, `scope` to `"General"`, and `authorized` to `false`.
Only an authorized, non-withdrawn current revision is eligible for retrieval.
Authorization is a local administrator decision, not automatic trust in content.

`procedure_id` holds a business identifier, such as `"24"` or `"IT-24"`. Numeric
leading zeros are normalized. When omitted, a unique explicit identifier in the
text is inferred; multiple detected identifiers require explicit primary metadata
or separate documents. One primary procedure per document is supported.

`procedure_key` identifies the same logical procedure across separate documents
and versions. Supply the same key for competing versions. If omitted, scope,
procedure ID and title form the key. Use different keys for unrelated procedures
sharing ID 24. Metadata accuracy matters: unrelated titles cannot reliably be
recognized as conflicting versions without a shared key.

Update the metadata JSON's version and upload a new file to the existing UUID:

```powershell
curl.exe --silent --show-error --fail -X PUT `
  -F "file=@D:\ITGlasses\tests\fixtures\knowledge\windows.md" `
  -F "metadata=<D:\ITGlasses\tests\fixtures\knowledge\windows.metadata.json" `
  "http://127.0.0.1:8000/api/v1/documents/$($doc.id)"

Invoke-RestMethod "http://127.0.0.1:8000/api/v1/documents/$($doc.id)/reindex" -Method Post

Invoke-RestMethod "http://127.0.0.1:8000/api/v1/documents/$($doc.id)/authorization" `
  -Method Patch -ContentType 'application/json' -Body '{"authorized":false}'

Invoke-RestMethod "http://127.0.0.1:8000/api/v1/documents/$($doc.id)/withdraw" -Method Post
```

`authorized:true` can reauthorize a non-withdrawn document. Withdrawal is final
for that UUID; create a new document if it must be reintroduced. `DELETE
/api/v1/documents/{id}` also withdraws logically. Originals, older revisions and
their index rows are retained; there is no physical deletion or automatic cleanup.
Reindexing creates a new revision and preserves current authorization.

Pending conversation candidates include revision numbers. Every follow-up and
source request checks current eligibility again. Old source URLs return 404 after
revocation, withdrawal or replacement. Already delivered answers cannot be recalled
from a client's memory; the server does not reuse their excerpts as future evidence.

## Endpoints

| Method and path | Purpose |
| --- | --- |
| `GET /api/v1/documents` | List local managed metadata, including withdrawn records |
| `POST /api/v1/documents` | Upload multipart file and JSON metadata |
| `PUT /api/v1/documents/{id}` | Add a revision to an existing document |
| `PATCH /api/v1/documents/{id}/authorization` | Grant or revoke retrieval eligibility |
| `POST /api/v1/documents/{id}/withdraw` | Withdraw from retrieval |
| `DELETE /api/v1/documents/{id}` | Alias for logical withdrawal |
| `POST /api/v1/documents/{id}/reindex` | Re-extract and rebuild current vectors |
| `POST /api/v1/knowledge/chat` | Grounded query or conversational clarification |
| `GET /api/v1/knowledge/sources/{citation_id}` | Reauthorized exact evidence and provenance |
| `GET /api/v1/knowledge/status` | Embedding availability and eligible document count |

The original `/health`, `/api/v1/status`, and `/api/v1/chat` are preserved.
Errors retain `{"error":{"code":"...","message":"..."}}` without reflecting
document text. Examples: 400 unsupported format, 413 file limit, 422 invalid
input/extraction, 409 expired conversation or reindex required, 503 unavailable
embeddings, 504 model timeout, 502 invalid evidence selection. Uploads require
Content-Length (411 if absent). Missing models are never downloaded automatically.

## Storage and retrieval architecture

All paths are relative to `D:\ITGlasses`:

```text
backend/app/knowledge/
  models.py       # Request/response and metadata contracts
  routes.py       # Local REST API
  extract.py      # Validated extraction subprocess and chunking
  embeddings.py   # Local CPU embedding provider
  store.py        # SQLite metadata, revisions, vectors and FTS5
  retrieval.py    # Exact IDs, lexical/semantic ranking and intent
  grounding.py    # Constrained LLM evidence selection
  service.py      # Access checks, conversations and orchestration
documents/managed/<document UUID>/<revision>-<unique ID>/source.<extension>
storage/knowledge/knowledge.sqlite3
storage/models/                       # Existing Ollama models
storage/acceptance/                   # Isolated synthetic verification corpora
.cache/tmp/                           # Process-local parser/upload temporary files
.cache/pip/
tests/fixtures/knowledge/              # Public synthetic examples only
```

SQLite WAL transactions store stable document UUIDs, immutable revision hashes,
version, scope, title, primary procedure identity, source page/section/paragraph
and 1024-dimensional float32 vectors. FTS5 BM25 ranks exact terms, including
technical identifiers. Reciprocal rank fusion combines lexical ranking with
normalized embedding cosine similarity. Exact procedure-ID ambiguity is resolved
before any embedding or LLM call. Retrieval filters eligible current revisions.

`qwen3-embedding:0.6b` runs with `num_gpu=0`, four CPU threads, batches of four,
2048 context tokens, and truncation disabled. Embeddings are unloaded before
evidence selection to reduce concurrent memory pressure. Index entries retain
the model digest; a changed digest requires reindexing.

The existing Qwen3 Instruct model receives only a bounded question and two
retrieved chunks with instructions to treat all input as untrusted data. Its
output is validated against existing passage IDs. A second authorization check
precedes response assembly. No document command is executed.

## Limits and deployment boundaries

- Local single-user administration only. The authorization flag is not user or
  group ACL enforcement. Any trusted local client can administer the corpus.
  Authentication, tenant separation and enterprise RBAC remain future work.
  Do not expose these services on a LAN or the internet.
- One backend worker serializes queries and mutations. Conversations are in
  memory (100 maximum, 30-minute inactivity expiry), with bounded candidate and
  revision context. Restart expires them. General conversational reasoning and
  arbitrary cross-document synthesis are not implemented.
- UTF-8 TXT/Markdown, text-based PDF and DOCX are supported. No OCR. PDF pages
  are retained; DOCX pages are not invented. Complex layouts/tables can extract
  imperfectly and require source review.
- Maximum file 10 MiB, 300 PDF pages, 500,000 extracted characters, 1,000 chunks
  per file, 900 characters per chunk, extraction deadline 30 seconds. DOCX
  archives have entry/expanded-size limits. Extraction subprocess isolation is
  not a complete operating-system sandbox or hard memory quota.
- Up to 16,000 question characters are searched in segments. Queries over 1,800
  characters request a narrower clarification after retrieval to respect the
  small generation context. This prioritizes abstention over silent truncation.
- Similarity thresholds were evaluated on a small synthetic corpus, not a
  representative company collection. Wider synonym coverage and relevance need
  evaluation on an approved, labeled corpus. Conflicts are conservatively flagged
  by procedure identity/version, not by a general contradiction proof.
- Vector search scans eligible chunks in memory; it is intended for an initial
  modest corpus. Raw vectors require about 39 MiB per 10,000 chunks, before text,
  SQLite/FTS overhead and in-memory copies. Large-corpus performance is unmeasured.
- Exact excerpts make citations verifiable but do not guarantee that an uploaded
  policy is correct or that every model relevance decision is correct. Prompt
  injection cannot execute tools or invent new citation IDs; quoted malicious
  text remains untrusted and must not be treated as an action instruction.
- Documents/indexes are local and Git-ignored, not encrypted by this application.
  Original revisions and failed staged uploads are retained and consume disk.
  Back up managed documents and the SQLite database together while stopped.
- No web interface, Android changes, speech features or Phase 4 implementation.

## Verification commands

```powershell
Set-Location D:\ITGlasses
$env:TEMP = 'D:\ITGlasses\.cache\tmp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = 'D:\ITGlasses\.cache\pip'
.\.venv\Scripts\python.exe -m pytest -q -o cache_dir=.cache/pytest
.\.venv\Scripts\python.exe -m pip check

# Optional real-model checks; Ollama must already be running.
.\.venv\Scripts\python.exe tests\verify_knowledge_acceptance.py
.\.venv\Scripts\python.exe tests\smoke_knowledge.py
```

The optional checks create isolated synthetic corpora under storage/acceptance,
require 6 GiB free RAM and approximately 3.5 GiB free VRAM, and request model
unloading when finished. Run when no other client is using Ollama.
The HTTP probe binds a temporary server to 127.0.0.1:8001 and stops only its own
child process. It leaves the normal backend and Ollama server running.
Detailed observed results are recorded in `docs/phase3-measurements.json` and
`docs/progress.md`.
