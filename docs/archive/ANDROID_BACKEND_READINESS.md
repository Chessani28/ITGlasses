# Android Backend Readiness

## Current architecture
- FastAPI backend in `backend/app/main.py`, entry `backend/run.py`.
- Ollama local, Qwen3 model.
- SQLite RAG, web search, voice TTS/STT.
- Unified assistant endpoint `/api/v1/assistant/chat`.

## Actual API endpoints and schemas
- GET /health -> {status:"ok"}
- GET /api/v1/status -> {status, provider, model, mock, rag_enabled, local_only}
- POST /api/v1/chat {message} -> {answer, provider, model, sources, documentation_sufficient}
- POST /api/v1/assistant/chat {message, session_id?} -> {answer, session_id, route, sources}
- Voice endpoints under /api/v1/voice (existing).

## Environment configuration
- ITGLASSES_HOST (default 127.0.0.1)
- ITGLASSES_PORT (default 8000)
- ITGLASSES_PROVIDER
- OLLAMA_BASE_URL (must remain loopback)
- ITGLASSES_ALLOW_LAN_BINDING (must be true for non-loopback host)

## LAN connection requirements
- Keep localhost default.
- For LAN testing set ITGLASSES_HOST to LAN IP and ITGLASSES_ALLOW_LAN_BINDING=true.
- Do not expose Ollama directly.
- No automatic firewall changes.

## Security requirements
- Android must authenticate before protected assistant access; current backend has no auth mechanism for Android. Implement token-based auth in later phase.
- No unauthenticated LAN assistant endpoint.
- Avoid permissive CORS.

## Android integration contract
- Availability: GET /health
- Text question: POST /api/v1/assistant/chat
- Session continuity via session_id field.
- TTS/STT via existing voice endpoints.
- Errors structured as {"error":{"code","message"}}.

## Steps to connect future Android app
1. Configure backend host/port via env.
2. Enable LAN binding explicitly.
3. Implement Android auth.
4. Point Android to backend URL.

## Known blockers and unverified assumptions
- No Android authentication implemented yet.
- No real Android device tested.
- TLS/transport security not configured.

## Changes made and tests executed
- Modified backend/app/core/config.py to support env-based host/port/ollama URL and gated LAN binding.
- Verified import and config behavior.
## Phase 2B verification
- HTTP tests passed for missing/invalid/valid tokens on assistant endpoint.
- Weaknesses: plaintext shared tokens, no expiry/revocation, unprotected document endpoints, no HTTPS.

## Phase security completion
- Protected assistant, voice, and document endpoints with Bearer auth.
- Full device enrollment, expiry, revocation remain pending (blocker).
- HTTPS pending certificate/IT approval.
