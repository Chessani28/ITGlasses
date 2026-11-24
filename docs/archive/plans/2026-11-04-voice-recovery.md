# Voice recovery implementation plan

Goal: repair the reproduced Windows reload HTTP 502 and improve capture/context while preserving the current user changes.
Architecture: retain Vosk and existing worker isolation. Add a compatible subprocess adapter only where asyncio subprocesses are unavailable. Keep routing in UnifiedAssistantService and capture in Segmenter.
Stack: Python/FastAPI/asyncio, Windows, Vosk, JavaScript AudioWorklet.
Spec: user's Voice Transcription Recovery request in this chat.
Constraints: no reset, commits, pushes, engine replacement, downloads, or unrelated refactoring. Work directly on D:/ITGlasses; user explicitly authorized implementation.

## Review focus
- Reload/SelectorEventLoop: valid WAV must transcribe, cancellation/timeout must reap the owned child.
- Broken worker: useful stage/type diagnostics, no transcript/internal paths in API errors.
- Interrupted phrases: only consecutive silence ends speech, preserve user's 2.5-second default.
- Ambiguity: technical aliases must not silently reinterpret ordinary Spanish.
- Context: printer follow-up retains subject, explicit manual request routes to RAG.

## Tasks
- [ ] Snapshot current changed files and baseline Python/JS tests.
- [ ] Add failing tests in tests/test_voice_recovery.py for selector subprocess, failures, invalid worker responses, safe diagnostics and timeout cleanup. Implement adapter in voice/service.py; retain worker exception handling with safe error metadata in worker.py. Verify real localhost endpoint under existing --reload.
- [ ] Add failing JS tests for cumulative pauses, settings, PCM duration and diagnostic units. Fix microphone.mjs and diagnostic UI wiring without removing UI.
- [ ] Add failing tests for printer topic continuity, explicit corrections and RAG priority; narrow changes to assistant/service.py. Add conservative spelled-acronym normalization and severe-quality clarification tests; retain raw text and user's mild-confidence diagnostics.
- [ ] Run full Python/JS suites, same synthetic WAVs and actual local endpoint/TTS. Do not claim physical microphone validation. Save Spanish report, actual metrics and before/after Git status.

Execution: inline in the current session, test-first per task. No commits, per explicit user constraint.

## Closure — 2026-11-05

Implementation and regression validation completed: Windows worker launch, capture, clarification, context, diagnostic replay. Preserved before snapshots. Additional authorized recovery of interleaved auth.py restored bearer-only API, retaining routes.py protection; incomplete sessions backed up, not enabled. Full Python: 243 passed; JavaScript: 34 passed; actual localhost health/status/silence/invalid-audio verified. Acoustic comparison deferred by required RAM gate (2.74 GiB < 3); real microphone and LAN deployment not validated. Independent review partial; two reported issues fixed and tested. Report: IMPLEMENTATION_REPORTS/VOICE_TRANSCRIPTION_RECOVERY.md. No commits/push.
