"""Real Vosk STT tests against the approved local model; no downloads are attempted.

These differ from tests/test_voice.py (which uses a FakeVoice) by exercising the
actual FastAPI transcription route and the real Vosk model through VoiceService.
They are skipped when the approved Spanish model is not installed.
"""
import array
import io
import sys
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.providers.mock import MockLLM
from backend.app.voice.service import VoiceService

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "storage" / "voice" / "models" / "vosk-model-small-es-0.42"
ACCEPTANCE_SAMPLE = (
    ROOT / "storage" / "voice" / "acceptance" / "speech-20261021-095722" / "case-0.wav"
)


def resample_to_16k(raw: bytes) -> bytes:
    """Resample mono 16-bit PCM WAV to 16 kHz (pure stdlib, no new deps)."""
    with wave.open(io.BytesIO(raw), "rb") as src:
        rate = src.getframerate()
        channels = src.getnchannels()
        sampwidth = src.getsampwidth()
        frames = src.readframes(src.getnframes())
    assert sampwidth == 2 and channels == 1, "acceptance sample must be mono 16-bit PCM"
    samples = array.array("h", frames)
    if sys.byteorder != "little":
        samples.byteswap()
    ratio = rate / 16000.0
    out = array.array("h")
    n = len(samples)
    i = 0.0
    while i < n - 1:
        i0 = int(i)
        frac = i - i0
        value = int(samples[i0] * (1.0 - frac) + samples[i0 + 1] * frac)
        out.append(max(-32768, min(32767, value)))
        i += ratio
    if n > 0 and int(i) >= n - 1:
        out.append(samples[n - 1])
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as dst:
        dst.setnchannels(1)
        dst.setsampwidth(2)
        dst.setframerate(16000)
        dst.writeframes(out.tobytes())
    return buffer.getvalue()


@pytest.fixture
def voice_client():
    if not (MODEL_DIR / "am" / "final.mdl").is_file():
        pytest.skip("Vosk Spanish model not installed; no download attempted.")
    voice = VoiceService()
    with TestClient(create_app(MockLLM(), voice)) as client:
        yield client, voice


@pytest.mark.skipif(not ACCEPTANCE_SAMPLE.is_file(), reason="Acceptance sample missing")
def test_real_transcribe_spanish_sample(voice_client):
    client, _ = voice_client
    raw = ACCEPTANCE_SAMPLE.read_bytes()
    wav_16k = resample_to_16k(raw)
    response = client.post(
        "/api/v1/voice/transcribe",
        content=wav_16k,
        headers={"Content-Type": "audio/wav"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("engine") == "vosk"
    text = body.get("text", "")
    assert isinstance(text, str) and text.strip(), "real transcription must be non-empty"


def test_real_transcribe_silence_returns_empty(voice_client):
    client, _ = voice_client
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 16000)  # 1 second of digital silence
    response = client.post(
        "/api/v1/voice/transcribe",
        content=output.getvalue(),
        headers={"Content-Type": "audio/wav"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("text", "") == ""
    assert "no_speech" in body.get("quality_reasons", [])


def test_real_transcribe_rejects_wrong_content_type(voice_client):
    client, _ = voice_client
    response = client.post(
        "/api/v1/voice/transcribe",
        content=b"not audio",
        headers={"Content-Type": "audio/webm"},
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "audio_format"


def test_real_transcribe_rejects_invalid_wav(voice_client):
    client, _ = voice_client
    # Wrong sample rate: backend requires 16 kHz mono 16-bit PCM WAV.
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * 8000)
    response = client.post(
        "/api/v1/voice/transcribe",
        content=output.getvalue(),
        headers={"Content-Type": "audio/wav"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "audio_format"
