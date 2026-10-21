"""Bounded CPU speech workers with project-local transient files."""
import asyncio
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import wave

from backend.app.core.config import PROJECT_ROOT
from backend.app.knowledge.models import KnowledgeError

MAX_AUDIO = 960044
VOICE_NAME = "es_ES-sharvard-medium"


def validate_audio(raw):
    if not 44 < len(raw) <= MAX_AUDIO:
        raise KnowledgeError("audio_size", "Audio must contain up to 30 seconds of mono 16 kHz PCM WAV.", 413)
    try:
        with wave.open(io.BytesIO(raw), "rb") as audio:
            frames = audio.getnframes()
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 16000, "NONE"):
                raise ValueError()
            if not 1600 <= frames <= 480000 or len(audio.readframes(frames)) != frames * 2:
                raise ValueError()
    except (wave.Error, EOFError, ValueError):
        raise KnowledgeError("audio_format", "Use complete mono 16-bit PCM WAV at 16 kHz, 0.1–30 seconds.", 422) from None


class VoiceService:
    def __init__(self, root=PROJECT_ROOT):
        self.root = Path(root)
        self.models = self.root / "storage/voice/models"
        self.temporary = self.root / ".cache/voice"
        self.busy = False

    def status(self):
        stt = importlib.util.find_spec("vosk") is not None and (self.models / "vosk-model-small-es-0.42/am/final.mdl").is_file()
        tts = importlib.util.find_spec("piper") is not None and all((self.models / (VOICE_NAME + suffix)).is_file() for suffix in (".onnx", ".onnx.json"))
        return {"stt_available": stt, "tts_available": tts, "stt_engine": "vosk", "tts_engine": "piper",
                "device": "cpu", "voices": [{"id": VOICE_NAME, "name": "Español · Sharvard", "speakers": [0, 1]}],
                "wake_detection": "experimental_transcription_matching", "busy": self.busy,
                "max_audio_seconds": 30, "max_text_characters": 6000}

    async def run(self, kind, payload, request=None):
        if not self.status()["stt_available" if kind == "stt" else "tts_available"]:
            raise KnowledgeError("voice_unavailable", "Install the approved local speech dependencies and models. No cloud fallback is used.", 503)
        if self.busy:
            raise KnowledgeError("voice_busy", "A local speech operation is already running. Try again after it finishes.", 409)
        self.busy = True
        process = None
        started = time.perf_counter()
        try:
            self.temporary.mkdir(parents=True, exist_ok=True)
            # Only files created by this request are removed on completion.
            with tempfile.TemporaryDirectory(prefix="request-", dir=self.temporary) as folder:
                folder = Path(folder)
                task = {"kind": kind, "models": str(self.models), "output": str(folder / "output.wav")}
                if kind == "stt":
                    (folder / "input.wav").write_bytes(payload)
                    task["input"] = str(folder / "input.wav")
                else:
                    task.update(payload)
                environment = {**os.environ, "TEMP": str(self.temporary), "TMP": str(self.temporary),
                               "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2", "PYTHONDONTWRITEBYTECODE": "1"}
                process = await asyncio.create_subprocess_exec(sys.executable, "-B", "-m", "backend.app.voice.worker",
                    cwd=PROJECT_ROOT, env=environment, stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                communication = asyncio.create_task(process.communicate(json.dumps(task).encode()))
                try:
                    async with asyncio.timeout(90):
                        while not communication.done():
                            if request is not None and await request.is_disconnected():
                                raise KnowledgeError("voice_cancelled", "Speech request cancelled.", 499)
                            await asyncio.sleep(0.05)
                        stdout, _ = await communication
                    if process.returncode:
                        raise KnowledgeError("voice_inference_failed", "The local speech engine failed. Check model files and compatibility.", 502)
                    result = json.loads(stdout)
                    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
                    if kind == "tts":
                        result["audio"] = Path(task["output"]).read_bytes()
                    return result
                finally:
                    if process.returncode is None:
                        process.kill()
                    await process.wait()
                    await communication
        except TimeoutError:
            raise KnowledgeError("voice_timeout", "Local speech processing exceeded 90 seconds.", 504) from None
        finally:
            self.busy = False
