"""Bounded CPU speech workers with project-local transient files."""
import asyncio
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
import sys
import subprocess
import re
import tempfile
import time
import wave

from backend.app.core.config import PROJECT_ROOT
from backend.app.knowledge.models import KnowledgeError
from .persistent_worker import (
    PersistentWhisperWorker,
    PersistentWorkerError,
    WorkerCrashedError,
    WorkerInferenceError,
    WorkerMemoryLimitError,
    WorkerProtocolError,
    WorkerResourceMonitorError,
    WorkerStartupError,
    WorkerTimeoutError,
    available_system_memory as _available_system_memory,
)

logger = logging.getLogger(__name__)

MAX_AUDIO = 960044
VOICE_NAME = "es_ES-sharvard-medium"
STT_ENGINES = {"vosk", "whisper-small"}
WHISPER_MIN_LOAD_RAM = 3 * 1024**3
WHISPER_ABORT_RAM = int(1.5 * 1024**3)


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


class _ThreadProcess:
    """Subprocess pipes for Windows SelectorEventLoop (Uvicorn reload).

    Only pipe I/O waits run in threads. The request loop retains timeout,
    disconnect detection and ownership of kill/wait. No global loop changes.
    """
    def __init__(self, process):
        self.process = process

    @property
    def returncode(self):
        return self.process.poll()

    async def communicate(self, data):
        return await asyncio.to_thread(self.process.communicate, data)

    def kill(self):
        self.process.kill()

    async def wait(self):
        return await asyncio.to_thread(self.process.wait)


async def _spawn_worker(*args, **kwargs):
    try:
        return await asyncio.create_subprocess_exec(*args, **kwargs)
    except NotImplementedError:
        if sys.platform != "win32":
            raise
        # Popen only starts the child here; communicate/wait never block the loop.
        process = subprocess.Popen(args, **kwargs, creationflags=subprocess.CREATE_NO_WINDOW)
        return _ThreadProcess(process)


def _safe_label(value):
    return re.sub(r"[^a-zA-Z0-9_]", "", str(value or "unknown"))[:64]


class VoiceService:
    def __init__(self, root=PROJECT_ROOT):
        self.root = Path(root)
        self.models = self.root / "storage/voice/models"
        self.temporary = self.root / ".cache/voice"
        self.busy = False
        self._whisper_active = False
        self._whisper_worker = None
        self._whisper_idle_task = None
        self._whisper_probe_cache = None
        self._whisper_lifecycle_lock = asyncio.Lock()

    def _vosk_available(self):
        return importlib.util.find_spec("vosk") is not None and (
            self.models / "vosk-model-small-es-0.42" / "am" / "final.mdl"
        ).is_file()

    def _whisper_configuration(self):
        python_value = os.environ.get("ITGLASSES_WHISPER_PYTHON", "").strip()
        model_value = os.environ.get("ITGLASSES_WHISPER_MODEL", "").strip()
        if not python_value or not model_value:
            missing = [
                name
                for name, value in (
                    ("ITGLASSES_WHISPER_PYTHON", python_value),
                    ("ITGLASSES_WHISPER_MODEL", model_value),
                )
                if not value
            ]
            return None, (
                "Whisper Small no está configurado en el proceso del backend. "
                f"Falta definir: {', '.join(missing)}."
            )
        python_path = Path(python_value).expanduser()
        model_path = Path(model_value).expanduser()
        if not python_path.is_absolute() or not model_path.is_absolute():
            return None, "Whisper paths must be absolute."
        python_path = python_path.resolve()
        model_path = model_path.resolve()
        if not python_path.is_file():
            return None, "Configured Whisper Python executable was not found."
        required_files = ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")
        if not model_path.is_dir() or any(
            not (model_path / name).is_file() for name in required_files
        ):
            return None, "Configured Whisper Small model is incomplete or missing."
        try:
            cache_signature = (
                str(python_path),
                python_path.stat().st_mtime_ns,
                python_path.stat().st_size,
                str(model_path),
                tuple(
                    (
                        name,
                        (model_path / name).stat().st_mtime_ns,
                        (model_path / name).stat().st_size,
                    )
                    for name in required_files
                ),
            )
        except OSError:
            return None, "Configured Whisper files could not be inspected."
        cached = self._whisper_probe_cache
        if (
            cached
            and cached[0] == cache_signature
            and time.monotonic() - cached[1] < 5
        ):
            return {"python": python_path, "model": model_path}, None
        environment = {
            **os.environ,
            "HF_HUB_OFFLINE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
        try:
            dependency_check = subprocess.run(
                [
                    str(python_path),
                    "-B",
                    "-c",
                    "import faster_whisper, av",
                ],
                cwd=str(self.root),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return None, "Whisper dependency check timed out."
        except OSError:
            return None, "Configured Whisper Python environment could not start."
        if dependency_check.returncode:
            return None, "faster-whisper and PyAV are unavailable in the configured environment."
        self._whisper_probe_cache = (cache_signature, time.monotonic())
        return {"python": python_path, "model": model_path}, None

    def _tts_available(self):
        return importlib.util.find_spec("piper") is not None and all(
            (self.models / (VOICE_NAME + suffix)).is_file()
            for suffix in (".onnx", ".onnx.json")
        )

    def status(self):
        vosk_available = self._vosk_available()
        whisper_config, whisper_reason = self._whisper_configuration()
        return {"stt_available": vosk_available or whisper_config is not None,
                "stt_engine": "vosk", "stt_engines": {
                    "vosk": {"available": vosk_available},
                    "whisper-small": {
                        "available": whisper_config is not None,
                        "reason": whisper_reason,
                    },
                },
                "tts_available": self._tts_available(), "tts_engine": "piper",
                "device": "cpu", "voices": [{"id": VOICE_NAME, "name": "Español · Sharvard", "speakers": [0, 1]}],
                "wake_detection": "experimental_transcription_matching", "busy": self.busy,
                "whisper_worker_loaded": bool(
                    self._whisper_worker and self._whisper_worker.alive
                ),
                "max_audio_seconds": 30, "max_text_characters": 6000}

    def _schedule_whisper_idle_shutdown(self, worker):
        task = self._whisper_idle_task
        if task and not task.done():
            task.cancel()
        self._whisper_idle_task = asyncio.create_task(
            self._monitor_whisper_idle(worker)
        )

    async def _monitor_whisper_idle(self, worker):
        try:
            while self._whisper_worker is worker and worker.alive:
                await asyncio.sleep(0.25)
                if self.busy:
                    continue
                try:
                    available_ram = await asyncio.to_thread(_available_system_memory)
                except OSError:
                    logger.exception("Could not monitor system memory for persistent Whisper")
                    await self._stop_whisper_worker(worker, forced=True)
                    return
                if self.busy:
                    continue
                if available_ram < WHISPER_ABORT_RAM:
                    logger.warning(
                        "Stopping persistent Whisper because available RAM fell below 1.5 GiB"
                    )
                    await self._stop_whisper_worker(worker, forced=True)
                    return
                if time.monotonic() - worker.last_used >= 60:
                    await self._stop_whisper_worker(worker)
                    return
        except asyncio.CancelledError:
            raise

    async def _stop_whisper_worker(self, worker=None, forced=False):
        async with self._whisper_lifecycle_lock:
            worker = worker or self._whisper_worker
            if worker is None:
                return
            if self._whisper_worker is worker:
                self._whisper_worker = None
            idle_task = self._whisper_idle_task
            self._whisper_idle_task = None
            if idle_task and idle_task is not asyncio.current_task() and not idle_task.done():
                idle_task.cancel()
                try:
                    await idle_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception("Persistent Whisper idle monitor failed")
            try:
                action = worker.terminate if forced or not worker.alive else worker.close
                await asyncio.to_thread(action)
            except Exception as error:
                logger.exception(
                    "Persistent Whisper shutdown failed; attempting forced cleanup"
                )
                try:
                    await asyncio.to_thread(worker.terminate)
                except Exception:
                    logger.exception("Forced persistent Whisper cleanup failed")
                    raise KnowledgeError(
                        "voice_worker_cleanup",
                        "Whisper worker processes could not be safely stopped.",
                        503,
                    ) from error
                if not forced:
                    raise KnowledgeError(
                        "voice_worker_cleanup",
                        "Whisper did not shut down cleanly; forced cleanup was required.",
                        503,
                    ) from error

    async def release_whisper(self):
        if self._whisper_active:
            raise KnowledgeError(
                "voice_busy",
                "Whisper transcription is active; retry the chat request when it finishes.",
                409,
            )
        await self._stop_whisper_worker()

    async def shutdown(self):
        await self._stop_whisper_worker()

    async def _start_persistent_whisper(self, worker):
        startup = asyncio.create_task(asyncio.to_thread(worker.start))
        try:
            return await asyncio.shield(startup)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(startup)
            except Exception:
                pass
            if worker.process:
                await asyncio.to_thread(worker.terminate)
            raise

    async def _run_persistent_whisper(self, payload, request):
        if self.busy:
            raise KnowledgeError(
                "voice_busy",
                "A local speech operation is already running. Try again after it finishes.",
                409,
            )
        self.busy = True
        self._whisper_active = True
        try:
            return await self._run_persistent_whisper_request(payload, request)
        finally:
            self._whisper_active = False
            self.busy = False
            worker = self._whisper_worker
            if worker and worker.alive:
                self._schedule_whisper_idle_shutdown(worker)

    async def _run_persistent_whisper_request(self, payload, request):
        config, reason = self._whisper_configuration()
        if config is None:
            if self._whisper_worker:
                await self._stop_whisper_worker(
                    self._whisper_worker,
                    forced=not self._whisper_worker.alive,
                )
            raise KnowledgeError("voice_engine_unavailable", reason, 503)
        try:
            validate_audio(payload)
        except KnowledgeError:
            raise
        worker = self._whisper_worker
        configuration = (str(config["python"]), str(config["model"]))
        if worker and (worker.configuration != configuration or not worker.alive):
            await self._stop_whisper_worker(worker, forced=not worker.alive)
            worker = None
        try:
            available_ram = _available_system_memory()
        except OSError:
            raise KnowledgeError(
                "voice_resource_monitor",
                "Available system memory cannot be measured safely.",
                503,
            ) from None
        if worker:
            if available_ram < WHISPER_ABORT_RAM:
                await self._stop_whisper_worker(worker, forced=True)
                raise KnowledgeError(
                    "voice_resource_limit",
                    "Whisper was stopped because available system memory fell below 1.5 GiB.",
                    503,
                )
        elif available_ram < WHISPER_MIN_LOAD_RAM:
            raise KnowledgeError(
                "voice_resource_limit",
                "Whisper Small requires at least 3 GiB of available system memory.",
                503,
            )
        if request is not None and await request.is_disconnected():
            raise KnowledgeError("voice_cancelled", "Speech request cancelled.", 499)

        started = time.perf_counter()
        try:
            if worker is None:
                worker = PersistentWhisperWorker(
                    config["python"],
                    config["model"],
                    PROJECT_ROOT,
                    self.temporary,
                )
                try:
                    startup = await self._start_persistent_whisper(worker)
                except WorkerMemoryLimitError as error:
                    raise KnowledgeError(
                        "voice_resource_limit", str(error), 503
                    ) from None
                except WorkerResourceMonitorError as error:
                    logger.error("Persistent Whisper startup resource check failed")
                    raise KnowledgeError(
                        "voice_resource_monitor",
                        "Whisper resource safety could not be verified before startup.",
                        503,
                    ) from error
                except PersistentWorkerError as error:
                    logger.error(
                        "Persistent Whisper startup failed error_type=%s",
                        _safe_label(type(error).__name__),
                    )
                    raise KnowledgeError(
                        "voice_worker_startup",
                        "The local Whisper worker could not start. Check its configuration and server log.",
                        503,
                    ) from None
                self._whisper_worker = worker
            else:
                startup = None

            inference = asyncio.create_task(
                asyncio.to_thread(worker.transcribe, payload)
            )
            try:
                while not inference.done():
                    if request is not None and await request.is_disconnected():
                        await asyncio.to_thread(worker.abort)
                        try:
                            await asyncio.shield(inference)
                        except PersistentWorkerError:
                            pass
                        self._whisper_worker = None
                        raise KnowledgeError(
                            "voice_cancelled", "Speech request cancelled.", 499
                        )
                    await asyncio.sleep(0.1)
                result = await asyncio.shield(inference)
            except asyncio.CancelledError:
                await asyncio.to_thread(worker.abort)
                try:
                    await asyncio.shield(inference)
                except Exception:
                    logger.exception(
                        "Persistent Whisper request failed during cancellation cleanup"
                    )
                self._whisper_worker = None
                raise

            if result.get("engine") != "whisper-small":
                raise WorkerProtocolError("worker engine identity did not match request")
            result["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
            result["minimum_available_ram_bytes"] = (
                worker.minimum_available_ram_bytes
            )
            memory = worker.memory_snapshot
            result["peak_working_set_mib"] = (
                round(worker.peak_working_set_bytes / 1024**2, 2)
            )
            result["peak_private_memory_mib"] = round(
                worker.peak_private_bytes / 1024**2, 2
            )
            if startup:
                result["worker_startup_seconds"] = startup["model_load_seconds"]
                result["worker_startup_total_seconds"] = worker.startup_seconds
            else:
                result["worker_startup_seconds"] = 0
            result["worker_memory"] = memory
            return result
        except KnowledgeError:
            if worker and not worker.alive:
                self._whisper_worker = None
            raise
        except WorkerTimeoutError as error:
            self._whisper_worker = None
            await self._stop_whisper_worker(worker, forced=True)
            logger.error("Persistent Whisper request timed out")
            raise KnowledgeError(
                "voice_timeout", "Local Whisper processing exceeded 90 seconds.", 504
            ) from error
        except WorkerMemoryLimitError as error:
            self._whisper_worker = None
            await self._stop_whisper_worker(worker, forced=True)
            logger.warning("Persistent Whisper stopped at the emergency RAM threshold")
            raise KnowledgeError("voice_resource_limit", str(error), 503) from None
        except WorkerResourceMonitorError as error:
            self._whisper_worker = None
            await self._stop_whisper_worker(worker, forced=True)
            logger.error("Persistent Whisper process resource monitoring failed")
            raise KnowledgeError(
                "voice_resource_monitor",
                "Whisper resource safety could not be monitored.",
                503,
            ) from error
        except (WorkerCrashedError, WorkerProtocolError, WorkerStartupError) as error:
            self._whisper_worker = None
            await self._stop_whisper_worker(worker, forced=True)
            logger.error(
                "Persistent Whisper worker failed error_type=%s",
                _safe_label(type(error).__name__),
            )
            raise KnowledgeError(
                "voice_inference_failed",
                "The local Whisper worker failed. No alternative engine was used.",
                502,
            ) from None
        except WorkerInferenceError as error:
            logger.error("Persistent Whisper inference failed")
            raise KnowledgeError(
                "voice_inference_failed",
                "The local Whisper worker could not transcribe this audio.",
                502,
            ) from None
        except Exception as error:  # noqa: BLE001 - contain subprocess/IO failures at the API boundary
            self._whisper_worker = None
            if worker:
                await self._stop_whisper_worker(worker, forced=True)
            logger.exception(
                "Persistent Whisper service failed error_type=%s",
                _safe_label(type(error).__name__),
            )
            raise KnowledgeError(
                "voice_inference_failed",
                "The local Whisper worker encountered an unexpected error.",
                502,
            ) from None


    async def run(self, kind, payload, request=None, engine="vosk"):
        if kind == "stt" and engine not in STT_ENGINES:
            raise KnowledgeError("voice_engine_invalid", "Select a supported local speech engine.", 422)
        if self.busy:
            raise KnowledgeError("voice_busy", "A local speech operation is already running. Try again after it finishes.", 409)
        if kind == "stt" and engine == "whisper-small":
            return await self._run_persistent_whisper(payload, request)
        if self._whisper_worker:
            await self.release_whisper()
        if kind == "stt":
            stt_available = self._vosk_available()
        else:
            stt_available = False
        if not (stt_available if kind == "stt" else self._tts_available()):
            raise KnowledgeError("voice_unavailable", "Install the approved local speech dependencies and models. No cloud fallback is used.", 503)
        self.busy = True
        process = None
        started = time.perf_counter()
        try:
            self.temporary.mkdir(parents=True, exist_ok=True)
            # Only files created by this request are removed on completion.
            with tempfile.TemporaryDirectory(prefix="request-", dir=self.temporary) as folder:
                folder = Path(folder)
                task = {
                    "kind": kind,
                    "engine": engine if kind == "stt" else "piper",
                    "models": str(self.models),
                    "output": str(folder / "output.wav"),
                }
                if kind == "stt":
                    (folder / "input.wav").write_bytes(payload)
                    task["input"] = str(folder / "input.wav")
                else:
                    task.update(payload)
                environment = {**os.environ, "TEMP": str(self.temporary), "TMP": str(self.temporary),
                               "OMP_NUM_THREADS": "2",
                               "OPENBLAS_NUM_THREADS": "2", "PYTHONDONTWRITEBYTECODE": "1"}
                process = await _spawn_worker(sys.executable, "-B", "-m", "backend.app.voice.worker",
                    cwd=PROJECT_ROOT, env=environment, stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                communication = asyncio.create_task(process.communicate(json.dumps(task).encode()))
                try:
                    async with asyncio.timeout(90):
                        while not communication.done():
                            if request is not None and await request.is_disconnected():
                                raise KnowledgeError("voice_cancelled", "Speech request cancelled.", 499)
                            await asyncio.sleep(0.05)
                        stdout, stderr = await communication
                    try:
                        result = json.loads(stdout)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        result = None
                    if process.returncode or (isinstance(result, dict) and result.get("error")):
                        error = result if isinstance(result, dict) else {}
                        logger.error("Voice worker failed kind=%s rc=%s stage=%s error_type=%s stderr_bytes=%d",
                                     kind, process.returncode, _safe_label(error.get("stage")),
                                     _safe_label(error.get("error_type")), len(stderr or b""))
                        raise KnowledgeError("voice_inference_failed",
                                             "The local speech worker failed. Check the server log for its stage and error type.", 502)
                    if not isinstance(result, dict) or (kind == "stt" and not isinstance(result.get("text"), str)):
                        logger.error("Voice worker invalid response kind=%s rc=%s", kind, process.returncode)
                        raise KnowledgeError("voice_inference_failed", "The local speech worker returned an invalid response.", 502)
                    if kind == "stt" and result.get("engine") != engine:
                        logger.error(
                            "Voice worker engine mismatch requested=%s returned=%s",
                            _safe_label(engine),
                            _safe_label(result.get("engine")),
                        )
                        raise KnowledgeError(
                            "voice_inference_failed",
                            "The local speech worker returned a different engine than requested.",
                            502,
                        )
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
        except KnowledgeError:
            raise
        except Exception as exc:  # noqa: BLE001 - catch subprocess/IO failures that would otherwise become HTTP 500
            logger.error("Voice service failure kind=%s error_type=%s loop=%s", kind, type(exc).__name__, type(asyncio.get_running_loop()).__name__)
            raise KnowledgeError("voice_inference_failed", "The local speech engine encountered an unexpected error.", 502) from None
        finally:
            self.busy = False


async def release_whisper_for_chat(request):
    voice = getattr(request.app.state, "voice", None)
    release = getattr(voice, "release_whisper", None)
    if release:
        await release()
