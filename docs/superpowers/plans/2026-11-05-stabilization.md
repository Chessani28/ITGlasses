# ITGlasses stabilization implementation plan

Goal: Repair confirmed failures while preserving FastAPI, SQLite/FTS, Ollama, Vosk/Piper and bearer authentication.
Architecture: Keep existing routes and service boundaries. Use the existing HTTPX dependency for bounded official Windows retrieval; preserve injectable public search backends. Return source excerpts rather than unvalidated generated company instructions.
Spec: User audit request, 2026-11-05.

Initial state: clean master, fb71a339afdaa207341f22fa2eae400e8f63cb75. No .venv, storage/documents/models or Ollama listener. 34 JS baseline tests pass. Python baseline pending isolated dependency installation.

- [x] Reproduce Python startup and test collection failures; replace undeclared requests usage with HTTPX and remove missing test helper dependency. Verify imports and full baseline.
- [x] Add RAG regressions using real SQLite and synthetic maintenance document, controlled embeddings/selector. Cite each returned excerpt, preserve permission/version checks, query a single evidence row with indexed SQL. Test revoked/superseded sources and natural follow-up.
- [x] Add voice regressions for explicit technical phrases, punctuation, nonfinite confidence, web-source synthesis and capture pause preservation. Preserve confidence policy and Vosk. Localize service errors and enable diagnostic logs.
- [x] Add internet regressions for exact requested Spanish question, internal-data refusal, no-result behavior, official version/build parsing, dates, redirects, response bounds, URL rejection. Fetch fixed official HTTPS Windows page without submitting query or history; retain general search fallback and disable unrestricted fetches.
- [x] Run all Python/JS tests, isolated real HTTP startup and acceptance A/B/C. Clearly distinguish stub inference, synthetic documents and real network retrieval; do not load models in low memory.
- [x] Inspect final diff, verify tracked tests and Git safety; deliver Spanish audit, counts and limitations. No push or automatic commit.

Security limits: existing authorization is single-user document eligibility, not enterprise RBAC. No login replacement or new identity system. No unrelated features, downloads of models, architectural changes or exposure to LAN.

Results: 162 Python and 36 JS tests pass; 8 actual HTTP auxiliary checks pass. Actual acceptance C passes with live official Microsoft evidence; A/B do not meet functional acceptance because documents/Ollama are absent. Synthetic RAG and mocked Ollama adapter regressions pass; no human-audio inference performed. Independent review findings corrected and regression tested. No commit or push.
Ruling: Leave branch as-is, uncommitted, matching user review/no-push requirements. Full functional readiness remains an environment-dependent follow-up, explicitly documented in the report.
