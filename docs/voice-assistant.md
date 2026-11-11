# Local voice assistant — Phase 5

## Scope and current acceptance status

The Windows voice pipeline uses browser PCM capture, CPU-only Vosk recognition by
default, an optional explicitly selected Whisper Small pilot, the existing chat
endpoints, and CPU-only Piper synthesis. It has no cloud speech fallback and does
not use browser SpeechRecognition or speechSynthesis services. Vosk remains the
default and Whisper errors never trigger a silent engine fallback.

## Architecture

1. The user explicitly activates the microphone on localhost and grants permission.
2. An AudioWorklet captures mono audio. Energy detection retains speech and closes
   a turn after 1.1 seconds of silence, with a maximum 28-second recording.
3. The browser converts PCM to mono 16-bit, 16 kHz WAV and calls `/api/v1/voice/transcribe`.
4. The selected STT engine runs in an isolated CPU process. Whisper Small uses the
   configured local Python environment, CPU INT8 and local-only model loading.
5. The transcript is displayed in an editable review box. Chat is not called until
   the user confirms the text; discarding or stopping does not submit it.
6. The shared web chat function calls `/api/v1/chat` or `/api/v1/knowledge/chat`.
7. The unchanged chat UI displays the full response, clarification choices and sources.
8. `/api/v1/voice/synthesize` generates WAV using Piper in a separate CPU process.
9. The browser plays the response, then opens a configurable 15/30/60-second window.

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
| STT default | `vosk-model-small-es-0.42` | About 39 MB download; Apache 2.0; unchanged lightweight CPU Spanish recognition. |
| STT experimental | `Systran/faster-whisper-small`, CPU INT8 | Separate local environment/model; on one 20-utterance, one-speaker provisional set, WER was 24.60%, mean inference about 2.01 seconds, and peak private process memory about 2.76 GiB. Not evidence of broad accuracy or accent robustness. |
| TTS | Piper 1.8.0, `es_ES-sharvard-medium` | About 64 MB voice; two Spain-Spanish speakers. Piper engine is GPL-3.0. The voice card declares CC BY 3.0 for its dataset and notes fine-tuning from Lessac. Preserve attribution and review the complete model provenance before redistribution. |
| Wake phrase | Explicit transcription-prefix matcher | Uses the existing Vosk transcript; no additional model. Experimental, vulnerable to misses and false activations. |

Vosk 0.3.45 offers a `py3-none-win_amd64` wheel and Piper 1.8.0 a
`cp39-abi3-win_amd64` wheel. Availability was checked against PyPI; actual Python
3.13 operation still requires installation and real inference tests.

Whisper is never installed into the production Python environment by this pilot.
Technical names, acronyms, negations and Spanish speech from additional speakers
need representative human evaluation; do not assume either model is reliable for
meaning-critical requests based on the provisional sample.

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

### Optional Whisper Small pilot

The production dependency manifests are unchanged. Whisper remains optional;
without its two paths Vosk continues to work as before. To make the pilot
reproducible across PowerShell sessions, copy `scripts\runtime-config.example.psd1`
to `runtime-config.psd1` in the project root if the local file does not already
exist. This file is ignored by Git and must never contain credentials:

```powershell
Set-Location D:\ITGlasses
if (-not (Test-Path .\runtime-config.psd1)) {
    Copy-Item .\scripts\runtime-config.example.psd1 .\runtime-config.psd1
}
notepad .\runtime-config.psd1
```

Set `WhisperPython` and `WhisperModel` to the operator-verified absolute paths,
or leave both blank to use Vosk only. Startup verifies the executable, model
directory, and required model files before launching. Environment variables
already set in the launching PowerShell session take precedence over this file.
Do not assume paths from an example or an older backend process are correct.

The environment must contain compatible `faster-whisper` and PyAV packages. The
model directory must contain `model.bin`, `config.json`, `tokenizer.json` and
`vocabulary.txt`. The application checks these paths and probes the configured
interpreter without loading the model. Inference uses `local_files_only=True` and
`HF_HUB_OFFLINE=1`; it does not download packages or model files.

Vosk remains selected unless the user explicitly chooses **Whisper Small ·
experimental** in Voice Preferences. The selector sends `X-ITGlasses-STT-Engine`;
clients that omit this header continue to use Vosk. The transcript must be reviewed
and confirmed or edited before it is submitted to chat. Whisper requires at least
3 GiB of available system RAM before startup. It checks system RAM and the actual
inference process, and forcibly shuts down its owned process tree if available RAM
drops below 1.5 GiB. Each request retains the 90-second deadline; disconnect and
timeout terminate the owned worker. The worker uses bounded JSON-lines over private
stdin/stdout pipes, with no network listener, audio/transcript files, automatic
downloads, or inference concurrency. It is shut down after 60 seconds idle, on
backend shutdown, and before Vosk/Piper or chat/knowledge inference. Chat requests
arriving during an active Whisper transcription receive 409 rather than competing
for memory with it. Whisper failures never retry through Vosk.

The isolated prototype benchmark completed ten sequential requests at a 1.95-second
mean, compared with 4.39 seconds for one-process-per-request (55.5% lower in that
test); all ten persistent outputs matched their per-request counterparts. Peak
private worker memory was about 2.44 GiB. This is not a measurement of the
production-integrated API path or a representative accuracy evaluation. The earlier
20-recording, one-speaker provisional result was 24.60% WER; it must not be
generalized to other speakers, accents, or meaning-critical terms. Ollama was not
tested under concurrent inference. The experimental selector remains opt-in.

To return to Vosk, select **Vosk · predeterminado**. To disable the pilot entirely,
leave both Whisper paths blank in the local runtime config (or remove both
environment overrides) and restart the backend. No production model or package
needs to be removed.

### Safe runtime configuration and restart

`runtime-config.psd1` stores only non-secret local settings:

- `WhisperPython` and `WhisperModel`: both absolute local paths, or both blank.
- `RequireAuth`: set to `$true` when bearer authentication is required.
- `AllowLanBinding`: keep `$false` for loopback-only use. LAN binding requires a
  bearer token and an explicit application LAN configuration.

Inherited process environment variables override the local runtime file.
`config.toml` supplies application defaults, with its supported environment
overrides taking precedence. The launcher’s explicit `-Provider` argument
continues to select the Ollama or mock provider.

When authentication is required, provide `ITGLASSES_API_TOKENS` through the
operator-controlled launching environment, or provision one token for the
current Windows user using `.\scripts\set-api-token.ps1`. The script reads it
without echoing it and stores only Windows DPAPI-protected data in the ignored
`.runtime` directory. The protected token cannot be reused under another Windows
user. Existing stored tokens are never replaced unless `-Replace` is supplied
explicitly. Never put tokens in `runtime-config.psd1`, command-line arguments,
source code, or logs.

For a restart after reviewing the current process and choosing to stop only the
ITGlasses backend:

1. Confirm the local runtime file has the intended Whisper paths and auth mode.
   If auth is required, confirm the operator token is available in the launching
   environment or in the current user's protected token store.
2. From `D:\ITGlasses`, run `.\scripts\stop-backend.ps1`. This targets project
   backend Python processes; it does not stop Ollama.
3. Run `.\scripts\ensure-backend.ps1 -Provider ollama`. It loads and validates
   the local configuration before spawning the backend, then returns after
   `/health` is ready.
4. Verify `/health`, `/api/v1/status`, and `/api/v1/voice/status`. Supply bearer
   authentication to protected routes through a secure client mechanism; do not
   put tokens in shell history. Confirm `whisper-small.available` matches the
   configured paths and Ollama reports the intended local model.

If configuration is incomplete or invalid, startup stops with a sanitized
message and does not disable required authentication. A healthy existing
backend is reused by `ensure-backend.ps1`; stop it explicitly before using that
script to load changed startup settings.

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
| `GET /api/v1/voice/status` | Installed model/dependency presence, per-engine availability, default engine (Vosk), CPU device, supported voice/speakers and limits. Presence is not an inference health check. |
| `POST /api/v1/voice/transcribe` | Raw `audio/wav`: mono PCM16, 16 kHz, 0.1–30 seconds. Optional `X-ITGlasses-STT-Engine: whisper-small`; omitted or `vosk` selects Vosk. Returns raw/normalized text, actual engine, elapsed milliseconds and Whisper resource measurements when available. |
| `POST /api/v1/voice/synthesize` | JSON `text` (1–6000 characters), optional `voice`, `speaker` (0/1), `speed` (0.75–1.5), `citation_ids` (up to 20 UUIDs). Returns WAV with no-store and timing/resource headers. |

Only one speech operation may run at a time; concurrent speech gets 409. Each
worker has a 90-second deadline. No arbitrary filesystem paths, commands or URLs
are accepted. Errors are sanitized. Browser cross-origin speech requests are
rejected. The existing backend binds exclusively to 127.0.0.1.

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
asynchronous results after Stop, audio conversion and silence segmentation. The
persistent-worker tests additionally cover model reuse, serialized requests, idle
and backend shutdown, worker failure/recovery, cancellation, timeout, memory gates,
engine identity, and Vosk/Piper compatibility.

Current verification: 260 backend tests and 34 JavaScript tests passed. One existing
Starlette/httpx deprecation warning remains. Ten recorded WAV requests completed
through the local FastAPI endpoint in one persistent process: first-request wall
latency 4.05 s, warm API mean 2.22 s (max 2.59 s), and mean Whisper inference 2.03 s.
The model loaded in 1.61 s. Outputs matched the archived persistent-worker outputs
10/10 and matched the saved baseline at the archived normalized-token criterion
9/10. Peak private memory was 2,499 MiB; minimum free system RAM was 3.14 GiB and
returned to 3.97 GiB after shutdown. The final idle snapshot was 2,358 MiB private
and 372 MiB working set. The owned launcher and actual inference PIDs were both
absent after graceful shutdown. A separate forced-termination/restart check
succeeded and left no old or replacement PIDs.

These are local single-machine measurements, not multi-speaker accuracy evidence.
Browser-to-API latency was not measured. An Ollama process was observed using about
94 MiB private memory; its loaded-model state was not inspected and no Ollama
inference request was sent. Concurrent Ollama/Whisper inference remains unvalidated.
`pip check` and the optional
Piper/Vosk/Ollama smoke probe were not run in this integration task.

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
