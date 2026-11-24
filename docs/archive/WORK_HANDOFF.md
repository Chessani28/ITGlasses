# WORK_HANDOFF.md

## Project
- Local path: D:\ITGlasses
- Existing repository: GitHub (origin master)
- Branch: master
- Relevant startup commands:
  - `python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000` (FastAPI backend)
  - `python start_backend.py` (alternative backend start)
  - `npm start` or `node frontend/ --port 3000` (frontend)
- Relevant application entry points:
  - `backend/app/main.py` (FastAPI app entry)
  - `backend/app/assistant/service.py` (UnifiedAssistantService, procedure matching)
  - `backend/app/providers/ollama.py` (Ollama chat provider)

## Working
- FastAPI backend starts successfully.
- Frontend loads.
- Local Ollama/Qwen3 general conversation works.
- UnifiedAssistantService compiles and imports.
- Spanish prefix stripping (`pasos de`, `procedures de`, `procedures a`) works in `match_procedures`.

## Broken

### Issue A: Authorized document retrieval
Question: "¿Cuáles son los 8 pasos de Computer Preparation según Training Setup Guideline?"
Observed response: "No se pudo consultar la documentación autorizada en este momento."
- The Spanish prefix `pasos de` is not being stripped before document matching.
- The `match_procedures` function needs `_strip_spanish_prefix()` integration (fixed in service.py).

### Issue B: Internet search
Question: "Busca en internet la versión más reciente de Windows 11."
Observed behavior: Unverified response with malformed source links.
- Web search functionality has malformed source links; retrieval not confirmed.

## Relevant files
- `backend/app/assistant/service.py` - `match_procedures()`, `_strip_spanish_prefix()`, `_tokens()`, `UnifiedAssistantService`
- `backend/app/assistant/memory.py` - Session store
- `backend/app/knowledge/retrieval.py` - Document retrieval
- `backend/app/knowledge/service.py` - Knowledge service
- `backend/app/providers/ollama.py` - Ollama chat provider

## Environment
- FastAPI on port 8000
- Ollama service required for chat functionality
- Configuration variables in `config.toml` and `backend/app/core/config.py`
- Do not include secrets, private manual contents, or database records in Git.

## Verification
- Syntax check: `python -m py_compile backend/app/assistant/service.py` - PASSES
- Import check: `from backend.app.assistant.service import UnifiedAssistantService, match_procedures, _strip_spanish_prefix` - PASSES
- Spanish prefix test: `_strip_spanish_prefix("pasos de WINDOWS DEPLOYMENT PROCEDURE")` → `WINDOWS DEPLOYMENT PROCEDURE` - PASS
- No-prefix English test: `match_procedures(procedures, "WINDOWS DEPLOYMENT PROCEDURE")` → matches - PASS
- Partial match test: `match_procedures(procedures, "WINDOWS DEPLOY")` → no match - PASS (correct behavior)
- Document query test: `"¿Cuáles son los 8 pasos de Computer Preparation según Training Setup Guideline?"` correctly matches COMPUTER PREPARATION after prefix stripping