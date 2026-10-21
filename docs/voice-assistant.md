# Local voice assistant — Phase 5

## Scope and current acceptance status

The Windows voice pipeline uses browser PCM capture, CPU-only Vosk recognition,
the existing chat endpoints, and CPU-only Piper synthesis. It has no cloud speech
fallback and does not use browser SpeechRecognition or speechSynthesis services.
Model installation and real-model acceptance are pending explicit approval of the
proposed downloads. Deterministic tests are not evidence of real recognition quality.

## Architecture

1. The user explicitly activates the microphone on localhost and grants permission.
2. An AudioWorklet captures mono audio. Energy detection retains speech and closes
   a turn after 1.1 seconds of silence, with a maximum 28-second recording.
3. The browser converts PCM to mono 16-bit, 16 kHz WAV and calls `/api/v1/voice/transcribe`.
4. Vosk runs in an isolated CPU process. That process exits before chat begins.
5. The shared web chat function calls `/api/v1/chat` or `/api/v1/knowledge/chat`.
6. The unchanged chat UI displays the full response, clarification choices and sources.
7. `/api/v1/voice/synthesize` generates WAV using Piper in a separate CPU process.
8. The browser plays the response, then opens a configurable 15/30/60-second window.

General chat remains stateless: subsequent utterances are accepted but earlier
messages are not injected into the LLM. Knowledge chat reuses the existing bounded,
expiring server conversation ID and clarification safeguards. No persistence is
added. Changing pages, hiding the tab, or pressing Stop releases the microphone.

The microphone stays acquired during processing/playback, but incoming samples are
discarded. This is half-duplex: use **Stop / interrupt** to cancel speech, then
Activate to ask another question. Spoken interruption while playback is active is
not implemented. Stop aborts client requests and terminates an owned speech worker
when the server observes disconnection; an already submitted LLM request may finish
on the server. No other application or Ollama process is terminated.

## Model choice and licensing

| Component | Selected option | Rationale and license |
| --- | --- | --- |
| STT | `vosk-model-small-es-0.42` | About 39 MB download; Apache 2.0; lightweight CPU Spanish recognition. Vosk documents about 300 MB runtime memory for small models. Actual measurements are still required. |
| TTS | Piper 1.8.0, `es_ES-sharvard-medium` | About 64 MB voice; two Spain-Spanish speakers. Piper engine is GPL-3.0. The voice card declares CC BY 3.0 for its dataset and notes fine-tuning from Lessac. Preserve attribution and review the complete model provenance before redistribution. |
| Wake phrase | Explicit transcription-prefix matcher | Uses the existing Vosk transcript; no additional model. Experimental, vulnerable to misses and false activations. |

Vosk 0.3.45 offers a `py3-none-win_amd64` wheel and Piper 1.8.0 a
`cp39-abi3-win_amd64` wheel. Availability was checked against PyPI; actual Python
3.13 operation still requires installation and real inference tests.

Faster-whisper was considered for stronger multilingual transcription, but its
CTranslate2 runtime and Whisper weights introduce a larger initial footprint.
Vosk is the conservative first CPU implementation for this 16 GB machine. Technical
names, acronyms and informal Mexican Spanish need a representative human speech
evaluation; do not assume the small model meets production accuracy requirements.

Dedicated offline wake engines such as openWakeWord require a suitable trained
Spanish/custom model; their pretrained model licenses differ from the code license.
No suitable model for this phrase has been validated or bundled. A transcription
matcher is not a production wake-word detector. The visible aliases are editable:
inspect actual transcripts before adding pronunciation variants. Matching requires
a phrase at the beginning; background conversation may still trigger it.

References (reviewed 2026-10-20):

- Vosk models and licenses: https://alphacephei.com/vosk/models
- Vosk package: https://pypi.org/project/vosk/
- Piper engine and GPL source: https://github.com/OHF-Voice/piper1-gpl
- Piper Python API: https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_PYTHON.md
- Voice attribution: https://huggingface.co/rhasspy/piper-voices/blob/main/es/es_ES/sharvard/medium/MODEL_CARD
- Faster-whisper: https://github.com/SYSTRAN/faster-whisper
- openWakeWord licenses: https://github.com/dscripka/openWakeWord

## Storage and installation

All runtime paths are relative to the existing project root on D:

```text
D:\ITGlasses\storage\voice\models\vosk-model-small-es-0.42\
D:\ITGlasses\storage\voice\models\es_ES-sharvard-medium.onnx
D:\ITGlasses\storage\voice\models\es_ES-sharvard-medium.onnx.json
D:\ITGlasses\.cache\voice\request-<random>\
D:\ITGlasses\.cache\pip\
```

Request-scoped audio is deleted only from the temporary directory created by that
request, after completion/cancellation. Existing documents, models and files are
never cleaned up. Audio playback uses memory-backed blob URLs, revoked on stop or
completion. Speech/transcript contents are not logged. There is no public audio
directory or replay endpoint. Offline operation requires models to be installed
before disconnecting the machine from the internet. Missing models return 503.

After the specific speech downloads have been approved, install only the optional
CPU inference requirements (no training extras):

```powershell
Set-Location D:\ITGlasses
$env:TEMP = 'D:\ITGlasses\.cache\tmp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = 'D:\ITGlasses\.cache\pip'
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m pip install -r requirements-voice.txt
.\.venv\Scripts\python.exe -m pip check
```

These variables affect only the current PowerShell session. Do not use setx.
Download the Spanish model ZIP from the Vosk model page and extract its named
directory to the model location above. Obtain the ONNX and matching JSON directly
from the voice's official Hugging Face directory. Preserve the model card and
license notices. No model is downloaded automatically by the application.

## Start and use

Use the existing Ollama startup instructions and, in a separate PowerShell window:

```powershell
Set-Location D:\ITGlasses
.\.venv\Scripts\python.exe -m backend.run
```

If an older backend is running, stop that backend with Ctrl+C in its terminal first.
Open http://127.0.0.1:8000/ and select Knowledge or General Chat. Expand the voice
panel, configure the window, speaker, speed and sensitivity, then Activate.
Manual listening is the default. Enable the experimental wake setting to wait for
the phrase. Pause after speaking. The transcript is visible even when wake matching
fails, so aliases can be adjusted. Longer pauses split questions into separate turns.

When a response needs clarification, speak the distinguishing description during
the follow-up window; use the existing chat choice buttons if recognition fails.
Sources remain inspectable in the text chat. Identification-only answers retain
their original intent and are not expanded into execution instructions.

Microphone permission may also be disabled in Windows privacy settings. Unsupported
browsers and unavailable/occupied microphones produce visible errors. If automatic
audio playback is blocked, press Play on the audio control or Stop. Playback is not
claimed successful merely because synthesis completed.

## API contracts and limits

| Endpoint | Contract |
| --- | --- |
| `GET /api/v1/voice/status` | Installed model/dependency presence, engine names, CPU device, supported voice/speakers and limits. Presence is not an inference health check. |
| `POST /api/v1/voice/transcribe` | Raw `audio/wav`: mono PCM16, 16 kHz, 0.1–30 seconds. Returns text, elapsed milliseconds and worker resource measurements when available. |
| `POST /api/v1/voice/synthesize` | JSON `text` (1–6000 characters), optional `voice`, `speaker` (0/1), `speed` (0.75–1.5), `citation_ids` (up to 20 UUIDs). Returns WAV with no-store and timing/resource headers. |

Only one speech worker may run at a time; concurrent speech gets 409. Each worker
has a 90-second deadline. No arbitrary filesystem paths, commands or URLs are
accepted. Errors are sanitized. Browser cross-origin speech requests are rejected.
The existing backend binds exclusively to 127.0.0.1.

Speech synthesis is a rendering API, not a second knowledge engine: caller-supplied
text is not certified as company policy. The official UI submits the actual backend
answer and all citation IDs. Sources are revalidated before and after synthesis and
immediately before playback. Revocation blocks subsequent playback generation;
audio already delivered or heard cannot be recalled. General answers are spoken
with an explicit general-knowledge disclaimer. Citation UUIDs are omitted from
speech while the original answer and references remain visible. Answers exceeding
the speech limit are not silently truncated; the full text remains in the chat.

## Verification and resources

Initial measured baseline: 6415.55 MiB free RAM; GPU 1655 MiB used and 4299 MiB free.
C: had 9,887,039,488 bytes free; D: had 767,628,742,656 bytes free. These are a
timestamped audit, not continuous monitoring or an inference benchmark.

Run deterministic tests using only synthetic inputs:

```powershell
Set-Location D:\ITGlasses
$env:TEMP = 'D:\ITGlasses\.cache\tmp'
$env:TMP = $env:TEMP
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
node --test tests/frontend.test.mjs tests/voice.test.mjs
```

Tests cover WAV validation, speech errors, citation revocation before/during TTS,
shared chat contracts, wake prefixes, follow-up windows, permission failures, stale
asynchronous results after Stop, audio conversion and silence segmentation.
Real STT accuracy, TTS audio quality, CPU/RAM peaks, model latency and physical
microphone playback remain acceptance checks, not mocked results.

Current verification: 114 Python tests passed in 29.13 seconds, including all 92
preexisting tests; 24 JavaScript tests passed. `pip check` reported no broken
requirements. The existing non-failing Starlette/httpx deprecation warning remains.
The actual localhost page was opened and its missing-model error was verified;
the microphone was not activated and no real speech inference was performed.
The optional `tests/smoke_voice.py` script is prepared for a real Piper → Vosk →
FastAPI → Ollama → Piper probe after approved installation. It checks model presence
and a 6 GiB RAM margin, uses synthetic generated speech, and records process CPU
seconds/peak working set plus system RAM/GPU snapshots under `storage/voice/acceptance`.
That probe has not yet been run; it does not replace a human microphone test.

## Future Android and Ray-Ban Meta boundary

Android should implement the same audio capture/playback adapters and reuse chat,
knowledge conversation IDs and citations. A later authenticated, restricted LAN
transport is required before changing the localhost boundary; it is not enabled now.

The current official DAT pages describe Android/iOS access to camera, microphone
and audio capabilities. The official Android repository documents permissions and
PCM audio streams. These are published SDK capabilities, not hardware verification
on this machine or confirmation of compatibility with the user's glasses/firmware.

- https://developers.meta.com/wearables/device-access-toolkit/
- https://developers.meta.com/wearables/faq/
- https://github.com/facebook/meta-wearables-dat-android

Before implementation, inspect the exact sample/SDK version and test registration,
authorization, microphone permissions, Bluetooth/audio routing, interruptions and
supported formats with actual glasses. This Windows prototype neither replaces
the native Hey Meta wake phrase nor proves glasses connectivity. No Android or
glasses project is modified during this phase.
