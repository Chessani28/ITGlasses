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
    COMPUTE_TYPES,
    WHISPER_DEVICES,
    PersistentWhisperWorker,
    PersistentWorkerError,
    WorkerCrashedError,
    WorkerGpuError,
    WorkerInferenceError,
    WorkerMemoryLimitError,
    WorkerProtocolError,
    WorkerResourceMonitorError,
    WorkerStartupError,
    WorkerTimeoutError,
    available_system_memory as _available_system_memory,
    model_files as whisper_model_files,
)
from . import vocabulary as voice_vocabulary
from .correction import apply_to_result as _correct_transcript

logger = logging.getLogger(__name__)

MAX_AUDIO = 960044
VOICE_NAME = "es_ES-sharvard-medium"
STT_ENGINES = {"vosk", "whisper-small"}
WHISPER_MIN_LOAD_RAM = 3 * 1024**3
WHISPER_ABORT_RAM = int(1.5 * 1024**3)
# Keep the persistent Whisper loaded between voice questions only with plenty of free RAM (chat and speech
# synthesis otherwise unload it so Ollama and Piper get the memory). Conservative default; never below the load
# limit. ITGLASSES_WHISPER_KEEP_RAM_GIB overrides it.
WHISPER_KEEP_RAM_DEFAULT = 8 * 1024**3


def whisper_keep_ram_bytes(value=None):
    """Free RAM (bytes) required to keep Whisper loaded: the environment value in GiB, at least 3 GiB.

    Missing, invalid, zero or negative values give the conservative default (8 GiB).
    """
    raw = os.environ.get("ITGLASSES_WHISPER_KEEP_RAM_GIB") if value is None else value
    if raw is None or str(raw).strip() == "":
        return WHISPER_KEEP_RAM_DEFAULT
    try:
        gib = float(str(raw).strip())
    except ValueError:
        logger.warning("ITGLASSES_WHISPER_KEEP_RAM_GIB is not a number; using the 8 GiB default")
        return WHISPER_KEEP_RAM_DEFAULT
    if gib != gib or gib in (float("inf"), float("-inf")) or gib <= 0:
        logger.warning("ITGLASSES_WHISPER_KEEP_RAM_GIB must be positive; using the 8 GiB default")
        return WHISPER_KEEP_RAM_DEFAULT
    requested = int(gib * 1024**3)
    if requested < WHISPER_MIN_LOAD_RAM:
        logger.warning("ITGLASSES_WHISPER_KEEP_RAM_GIB is below the 3 GiB load limit; using 3 GiB")
        return WHISPER_MIN_LOAD_RAM
    return requested


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


# Voz 2: vocabulary hint for Whisper and post-transcription correction (Whisper and Vosk).
HINT_MODES = ("hotwords", "initial_prompt", "off")
_TRUE = {"1", "true", "on", "yes"}
_FALSE = {"0", "false", "off", "no"}


def whisper_hint_mode(value=None):
    """ITGLASSES_WHISPER_HINT_MODE: hotwords (default), initial_prompt or off; anything else gives hotwords."""
    raw = os.environ.get("ITGLASSES_WHISPER_HINT_MODE") if value is None else value
    mode = str(raw or "").strip().lower()
    if not mode:
        return "hotwords"
    if mode not in HINT_MODES:
        logger.warning("ITGLASSES_WHISPER_HINT_MODE is not hotwords, initial_prompt or off; using hotwords")
        return "hotwords"
    return mode


def stt_correction_enabled(value=None):
    """ITGLASSES_STT_CORRECTION: on by default; 0/false/off/no turn it off; anything else keeps it on."""
    raw = os.environ.get("ITGLASSES_STT_CORRECTION") if value is None else value
    flag = str(raw or "").strip().lower()
    if not flag or flag in _TRUE:
        return True
    if flag in _FALSE:
        return False
    logger.warning("ITGLASSES_STT_CORRECTION is not a boolean; keeping the correction on")
    return True


# Voz 3: Whisper large-v3-turbo on the GPU, Small on the CPU as the fallback, then Vosk in the app (503).
# "whisper" names the configured Whisper chain; "whisper-small" stays accepted and is what responses report.
STT_ENGINE_ALIASES = {"whisper": "whisper-small"}
WHISPER_MIN_VRAM_DEFAULT_MIB = 1000   # with Ollama loaded about 1249 MiB stay free on the RTX 2060
WHISPER_ABORT_VRAM_DEFAULT_MIB = 256
WHISPER_IDLE_CPU_SECONDS = 60
WHISPER_IDLE_GPU_DEFAULT_SECONDS = 900
WHISPER_IDLE_MIN_SECONDS = 30
GPU_COOLDOWN_SECONDS = 300
VRAM_CACHE_SECONDS = 1
VRAM_IDLE_CHECK_SECONDS = 5
GPU_MESSAGES = {
    "voice_gpu_unavailable": "Whisper cannot use the GPU (CUDA libraries, device or a recent GPU failure) and no CPU "
                             "fallback model is configured.",
    "voice_vram_limit": "There is not enough free GPU memory for Whisper and no CPU fallback model is configured.",
    "voice_worker_startup": "The local Whisper worker could not start on the GPU and no CPU fallback model is "
                            "configured.",
}


def _setting(name, value):
    return os.environ.get(name) if value is None else value


def whisper_device(value=None):
    """ITGLASSES_WHISPER_DEVICE: cpu (default) or cuda; anything else gives cpu."""
    device = str(_setting("ITGLASSES_WHISPER_DEVICE", value) or "").strip().lower()
    if not device:
        return "cpu"
    if device not in WHISPER_DEVICES:
        logger.warning("ITGLASSES_WHISPER_DEVICE is not cpu or cuda; using cpu")
        return "cpu"
    return device


def whisper_compute_type(device, value=None):
    """ITGLASSES_WHISPER_COMPUTE_TYPE for the device: int8 on the CPU and int8_float16 on CUDA by default."""
    allowed = COMPUTE_TYPES[device]
    compute_type = str(_setting("ITGLASSES_WHISPER_COMPUTE_TYPE", value) or "").strip().lower()
    if not compute_type:
        return allowed[0]
    if compute_type not in allowed:
        logger.warning("ITGLASSES_WHISPER_COMPUTE_TYPE is not valid for device=%s; using %s", device, allowed[0])
        return allowed[0]
    return compute_type


def _whole_number(name, value, default):
    raw = _setting(name, value)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        number = int(str(raw).strip())
    except ValueError:
        logger.warning("%s is not a whole number; using %d", name, default)
        return default
    if number < 0:
        logger.warning("%s must not be negative; using %d", name, default)
        return default
    return number


def whisper_min_vram_mib(value=None):
    """Free GPU memory (MiB) needed to load Whisper on CUDA (ITGLASSES_WHISPER_MIN_VRAM_MIB, default 1000)."""
    return _whole_number("ITGLASSES_WHISPER_MIN_VRAM_MIB", value, WHISPER_MIN_VRAM_DEFAULT_MIB)


def whisper_abort_vram_mib(value=None):
    """An idle GPU Whisper is unloaded below this free GPU memory (ITGLASSES_WHISPER_ABORT_VRAM_MIB, default 256)."""
    return _whole_number("ITGLASSES_WHISPER_ABORT_VRAM_MIB", value, WHISPER_ABORT_VRAM_DEFAULT_MIB)


def whisper_idle_seconds(device, value=None):
    """Inactivity before unloading: 60 s on the CPU (fixed); on CUDA ITGLASSES_WHISPER_IDLE_SECONDS, default 900,
    at least 30."""
    if device != "cuda":
        return WHISPER_IDLE_CPU_SECONDS
    seconds = _whole_number("ITGLASSES_WHISPER_IDLE_SECONDS", value, WHISPER_IDLE_GPU_DEFAULT_SECONDS)
    if seconds == 0:
        return WHISPER_IDLE_GPU_DEFAULT_SECONDS
    return max(seconds, WHISPER_IDLE_MIN_SECONDS)


def whisper_preload_enabled(value=None):
    """ITGLASSES_WHISPER_PRELOAD: load Whisper when the backend starts. Off by default."""
    flag = str(_setting("ITGLASSES_WHISPER_PRELOAD", value) or "").strip().lower()
    if not flag or flag in _FALSE:
        return False
    if flag in _TRUE:
        return True
    logger.warning("ITGLASSES_WHISPER_PRELOAD is not a boolean; preload stays off")
    return False


def whisper_fallback_model(value=None):
    """ITGLASSES_WHISPER_FALLBACK_MODEL: absolute folder of the CPU fallback (Small); None when unset or incomplete."""
    raw = str(_setting("ITGLASSES_WHISPER_FALLBACK_MODEL", value) or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        return None
    path = path.resolve()
    return path if whisper_model_files(path) is not None else None


def whisper_model_label(path):
    """Model name for responses and logs: the folder name without "faster-whisper-", never a path."""
    name = Path(str(path)).name
    if name.startswith("faster-whisper-"):
        name = name[len("faster-whisper-"):]
    return re.sub(r"[^a-zA-Z0-9._-]", "", name)[:64] or "unknown"


def query_gpu_memory():
    """Free, used and total MiB of GPU 0 from nvidia-smi; OSError when it cannot be read."""
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free,memory.used,memory.total",
             "--format=csv,noheader,nounits", "--id=0"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=5, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except subprocess.TimeoutExpired as error:
        raise OSError("nvidia-smi timed out") from error
    if completed.returncode:
        raise OSError("nvidia-smi failed")
    try:
        free, used, total = (int(float(part)) for part in (completed.stdout or "").strip().splitlines()[0].split(","))
    except (IndexError, ValueError) as error:
        raise OSError("nvidia-smi output was not understood") from error
    return {"free_mib": free, "used_mib": used, "total_mib": total}


class _GpuFailure(Exception):
    """Internal: the GPU worker could not start or failed; the request is retried once on the CPU fallback."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _whisper_targets(config):
    """(primary, fallback) worker targets. The fallback (Small, CPU, int8) only exists for a CUDA primary."""
    device = config.get("device", "cpu")
    primary = {
        "python": config["python"],
        "model": config["model"],
        "device": device,
        "compute_type": config.get("compute_type") or COMPUTE_TYPES[device][0],
    }
    fallback = None
    if device == "cuda" and config.get("fallback"):
        fallback = {"python": config["python"], "model": config["fallback"], "device": "cpu", "compute_type": "int8"}
    return primary, fallback


def _target_key(target):
    return ((str(target["python"]), str(target["model"])), (target["device"], target["compute_type"]))


def _worker_key(worker):
    # Workers without a runtime (built before Voz 3, test doubles) run on the CPU with int8.
    return (tuple(worker.configuration), tuple(getattr(worker, "runtime", ("cpu", "int8"))))


def _worker_device(worker):
    return _worker_key(worker)[1][0]


def _runtime_arguments(target):
    # CPU with int8 is the worker's default; any other runtime is passed explicitly.
    if (target["device"], target["compute_type"]) == ("cpu", "int8"):
        return {}
    return {"device": target["device"], "compute_type": target["compute_type"]}


def _safe_label(value):
    return re.sub(r"[^a-zA-Z0-9_]", "", str(value or "unknown"))[:64]


class VoiceService:
    def __init__(self, root=PROJECT_ROOT, knowledge_autoload=None):
        self.root = Path(root)
        # The production service (project root) may start the knowledge service for the vocabulary; tests with a
        # temporary root never touch the real knowledge base.
        self.knowledge_autoload = (self.root == Path(PROJECT_ROOT)) if knowledge_autoload is None else knowledge_autoload
        self._vocabulary = None
        self._vocabulary_signature = None
        self.models = self.root / "storage/voice/models"
        self.temporary = self.root / ".cache/voice"
        self.busy = False
        self._whisper_active = False
        self._whisper_worker = None
        self._whisper_idle_task = None
        self._whisper_probe_cache = None
        self._whisper_lifecycle_lock = asyncio.Lock()
        self._gpu_cooldown_until = 0.0
        self._vram_cache = None

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
        required_files = whisper_model_files(model_path)
        if required_files is None:
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
        device = whisper_device()
        config = {
            "python": python_path,
            "model": model_path,
            "device": device,
            "compute_type": whisper_compute_type(device),
            "fallback": whisper_fallback_model() if device == "cuda" else None,
        }
        cached = self._whisper_probe_cache
        if (
            cached
            and cached[0] == cache_signature
            and time.monotonic() - cached[1] < 5
        ):
            return config, None
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
        return config, None

    def _tts_available(self):
        return importlib.util.find_spec("piper") is not None and all(
            (self.models / (VOICE_NAME + suffix)).is_file()
            for suffix in (".onnx", ".onnx.json")
        )

    def status(self):
        vosk_available = self._vosk_available()
        whisper_config, whisper_reason = self._whisper_configuration()
        whisper_device_name = whisper_config["device"] if whisper_config else whisper_device()
        fallback = whisper_config.get("fallback") if whisper_config else None
        return {"stt_available": vosk_available or whisper_config is not None,
                "stt_engine": "vosk", "stt_engines": {
                    "vosk": {"available": vosk_available},
                    "whisper-small": {
                        "available": whisper_config is not None,
                        "reason": whisper_reason,
                        # Voz 3: model names only, never paths.
                        "model": whisper_model_label(whisper_config["model"]) if whisper_config else None,
                        "device": whisper_device_name,
                        "compute_type": (
                            whisper_config["compute_type"] if whisper_config
                            else whisper_compute_type(whisper_device_name)
                        ),
                        "fallback_available": fallback is not None,
                        "fallback_model": whisper_model_label(fallback) if fallback else None,
                        "gpu_paused": self._gpu_cooldown_active(),
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
        device = _worker_device(worker)
        idle_limit = whisper_idle_seconds(device)
        last_vram_check = time.monotonic()
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
                if device == "cuda" and time.monotonic() - last_vram_check >= VRAM_IDLE_CHECK_SECONDS:
                    last_vram_check = time.monotonic()
                    memory = await asyncio.to_thread(self._gpu_memory)
                    if self.busy:
                        continue
                    if memory is not None and memory["free_mib"] < whisper_abort_vram_mib():
                        logger.warning(
                            "Unloading persistent Whisper: free GPU memory fell below the abort threshold"
                        )
                        await self._stop_whisper_worker(worker)
                        return
                if time.monotonic() - worker.last_used >= idle_limit:
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
        """Unload Whisper before chat or another speech engine, unless free RAM is above the keep threshold.

        A Whisper on the GPU (Voz 3) is always kept: its weights are in VRAM, next to Ollama's. shutdown() and
        configuration changes always unload; the idle monitor still unloads after its idle time, below 1.5 GiB of
        RAM or, on the GPU, below the free-VRAM abort threshold. If memory cannot be measured, a CPU Whisper is
        unloaded.
        """
        if self._whisper_active:
            raise KnowledgeError(
                "voice_busy",
                "Whisper transcription is active; retry the chat request when it finishes.",
                409,
            )
        worker = self._whisper_worker
        if worker is not None and worker.alive and _worker_device(worker) == "cuda":
            logger.info("Keeping persistent Whisper loaded on the GPU")
            return
        if worker is not None and worker.alive:
            try:
                available_ram = _available_system_memory()
            except OSError:
                available_ram = None
            if available_ram is not None and available_ram >= whisper_keep_ram_bytes():
                logger.info("Keeping persistent Whisper loaded: available RAM is above the keep threshold")
                return
        await self._stop_whisper_worker()

    async def shutdown(self):
        await self._stop_whisper_worker()

    async def preload_whisper(self):
        """Voz 3 (ITGLASSES_WHISPER_PRELOAD, off by default): load the configured Whisper when the backend starts.

        Never loads the CPU fallback and never raises: failures are logged as codes and the first question loads
        Whisper as usual.
        """
        if self.busy or (self._whisper_worker and self._whisper_worker.alive):
            return False
        config, _ = await asyncio.to_thread(self._whisper_configuration)
        if config is None:
            logger.info("Whisper preload skipped: Whisper is not configured")
            return False
        if self.busy:
            return False
        primary, _ = _whisper_targets(config)
        self.busy = True
        self._whisper_active = True
        try:
            if primary["device"] == "cuda":
                memory = None if self._gpu_cooldown_active() else await asyncio.to_thread(self._gpu_memory)
                if memory is None or memory["free_mib"] < whisper_min_vram_mib():
                    code = "voice_gpu_unavailable" if memory is None else "voice_vram_limit"
                    logger.warning("Whisper preload skipped code=%s", code)
                    return False
            await self._whisper_worker_for(primary)
            logger.info("Whisper preloaded model=%s device=%s", whisper_model_label(primary["model"]),
                        primary["device"])
            return True
        except _GpuFailure as failure:
            self._start_gpu_cooldown(failure.code)
            return False
        except KnowledgeError as error:
            logger.warning("Whisper preload failed code=%s", _safe_label(error.code))
            return False
        except Exception as error:  # noqa: BLE001 - preload must never break the backend startup
            logger.warning("Whisper preload failed error_type=%s", _safe_label(type(error).__name__))
            return False
        finally:
            self._whisper_active = False
            self.busy = False
            worker = self._whisper_worker
            if worker and worker.alive:
                self._schedule_whisper_idle_shutdown(worker)

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

    def _vocabulary_for(self, request):
        """Vocabulary of the authorized knowledge base, rebuilt only when its signature changes; None if unavailable.

        Logged as counts only. Never returned to clients (the response only says whether a hint was used).
        """
        if request is None:
            return None
        state = getattr(getattr(request, "app", None), "state", None)
        knowledge = getattr(state, "knowledge", None)
        if knowledge is None and self.knowledge_autoload:
            try:
                from backend.app.knowledge.routes import service as knowledge_service
                knowledge = knowledge_service(request)
            except Exception as error:  # noqa: BLE001 - transcription must work without a vocabulary
                logger.warning("Voice vocabulary unavailable error_type=%s", _safe_label(type(error).__name__))
                return None
        if getattr(knowledge, "store", None) is None:
            return None
        try:
            signature = voice_vocabulary.signature(knowledge.store.documents(eligible_only=True))
            if self._vocabulary is None or signature != self._vocabulary_signature:
                self._vocabulary = voice_vocabulary.from_knowledge(knowledge, aliases=True, nicknames=True)
                self._vocabulary_signature = signature
                stats = self._vocabulary.stats()
                logger.info("Voice vocabulary rebuilt terms=%d chars=%d titles=%d headings=%d fixed=%d aliases=%d",
                            stats["terms"], stats["chars"], stats["title"], stats["heading"], stats["fixed"],
                            self._vocabulary.aliases)
            return self._vocabulary
        except Exception as error:  # noqa: BLE001
            logger.warning("Voice vocabulary unavailable error_type=%s", _safe_label(type(error).__name__))
            return None

    async def _run_persistent_whisper(self, payload, request, hint=None, hint_mode=None):
        if self.busy:
            raise KnowledgeError(
                "voice_busy",
                "A local speech operation is already running. Try again after it finishes.",
                409,
            )
        self.busy = True
        self._whisper_active = True
        try:
            return await self._run_persistent_whisper_request(payload, request, hint, hint_mode)
        finally:
            self._whisper_active = False
            self.busy = False
            worker = self._whisper_worker
            if worker and worker.alive:
                self._schedule_whisper_idle_shutdown(worker)

    def _start_gpu_cooldown(self, code):
        self._gpu_cooldown_until = time.monotonic() + GPU_COOLDOWN_SECONDS
        logger.warning("Whisper GPU paused seconds=%d code=%s", GPU_COOLDOWN_SECONDS, code)

    def _gpu_cooldown_active(self):
        return time.monotonic() < self._gpu_cooldown_until

    def _gpu_memory(self):
        """Free/used/total MiB of the GPU (nvidia-smi), cached for a second; None when it cannot be read."""
        cached = self._vram_cache
        if cached and time.monotonic() - cached[0] < VRAM_CACHE_SECONDS:
            return cached[1]
        try:
            memory = query_gpu_memory()
        except OSError as error:
            logger.warning("GPU memory unavailable error_type=%s", _safe_label(type(error).__name__))
            memory = None
        self._vram_cache = (time.monotonic(), memory)
        return memory

    async def _choose_whisper_target(self, primary, fallback):
        """(target, fallback_reason): the GPU model, or the CPU fallback while the GPU is paused, unreadable or
        short of free VRAM. A loaded GPU worker is used without measuring again."""
        if primary["device"] != "cuda":
            return primary, None
        worker = self._whisper_worker
        if worker and worker.alive and _worker_key(worker) == _target_key(primary):
            return primary, None
        if self._gpu_cooldown_active():
            reason = "voice_gpu_unavailable"
        else:
            memory = await asyncio.to_thread(self._gpu_memory)
            if memory is None:
                reason = "voice_gpu_unavailable"
                self._start_gpu_cooldown(reason)
            elif memory["free_mib"] < whisper_min_vram_mib():
                reason = "voice_vram_limit"
            else:
                return primary, None
        if fallback is None:
            raise KnowledgeError(reason, GPU_MESSAGES[reason], 503)
        return fallback, reason

    async def _run_persistent_whisper_request(self, payload, request, hint=None, hint_mode=None):
        config, reason = self._whisper_configuration()
        if config is None:
            if self._whisper_worker:
                await self._stop_whisper_worker(
                    self._whisper_worker,
                    forced=not self._whisper_worker.alive,
                )
            raise KnowledgeError("voice_engine_unavailable", reason, 503)
        validate_audio(payload)
        primary, fallback = _whisper_targets(config)
        target, fallback_reason = await self._choose_whisper_target(primary, fallback)
        try:
            result = await self._whisper_attempt(target, payload, request, hint, hint_mode)
        except _GpuFailure as failure:
            # The GPU failed while starting or transcribing: pause it and retry this request once on the CPU.
            self._start_gpu_cooldown(failure.code)
            if fallback is None:
                raise KnowledgeError(failure.code, GPU_MESSAGES[failure.code], 503) from None
            target, fallback_reason = fallback, failure.code
            result = await self._whisper_attempt(target, payload, request, hint, hint_mode)
        result["whisper_model"] = whisper_model_label(target["model"])
        result["device"] = target["device"]
        result["compute_type"] = target["compute_type"]
        result["fallback_reason"] = fallback_reason
        if fallback_reason:
            logger.warning("Whisper fallback used model=%s device=%s reason=%s",
                           result["whisper_model"], target["device"], fallback_reason)
        return result

    async def _whisper_worker_for(self, target, request=None):
        """(worker, startup) for the target: the loaded worker when it matches, otherwise a new one."""
        worker = self._whisper_worker
        if worker and (_worker_key(worker) != _target_key(target) or not worker.alive):
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
        if worker:
            return worker, None
        worker = PersistentWhisperWorker(
            target["python"],
            target["model"],
            PROJECT_ROOT,
            self.temporary,
            **_runtime_arguments(target),
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
                "Persistent Whisper startup failed error_type=%s device=%s",
                _safe_label(type(error).__name__),
                target["device"],
            )
            if target["device"] == "cuda":
                raise _GpuFailure(
                    error.code if isinstance(error, WorkerGpuError) else "voice_worker_startup"
                ) from None
            raise KnowledgeError(
                "voice_worker_startup",
                "The local Whisper worker could not start. Check its configuration and server log.",
                503,
            ) from None
        self._whisper_worker = worker
        return worker, startup

    async def _whisper_attempt(self, target, payload, request, hint=None, hint_mode=None):
        worker = None
        started = time.perf_counter()
        try:
            worker, startup = await self._whisper_worker_for(target, request)

            inference = asyncio.create_task(
                asyncio.to_thread(worker.transcribe, payload, hint=hint, hint_mode=hint_mode)
                if hint else asyncio.to_thread(worker.transcribe, payload)
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
        except _GpuFailure:
            raise
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
        except WorkerGpuError as error:
            self._whisper_worker = None
            await self._stop_whisper_worker(worker, forced=True)
            logger.error("Persistent Whisper GPU failure reason=%s", _safe_label(error.reason))
            if target["device"] == "cuda":
                raise _GpuFailure(error.code) from None
            raise KnowledgeError(
                "voice_inference_failed",
                "The local Whisper worker failed. No alternative engine was used.",
                502,
            ) from None
        except (WorkerCrashedError, WorkerProtocolError, WorkerStartupError) as error:
            self._whisper_worker = None
            await self._stop_whisper_worker(worker, forced=True)
            logger.error(
                "Persistent Whisper worker failed error_type=%s device=%s",
                _safe_label(type(error).__name__),
                target["device"],
            )
            if target["device"] == "cuda" and isinstance(error, WorkerCrashedError):
                raise _GpuFailure("voice_gpu_unavailable") from None
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
        """Speech operation. STT (Voz 2): Whisper gets the vocabulary hint (ITGLASSES_WHISPER_HINT_MODE) and every
        transcript, Whisper or Vosk, is corrected against known titles (ITGLASSES_STT_CORRECTION): ``text`` is the
        corrected transcript, ``raw_text`` the original. Logs carry counts only."""
        engine = STT_ENGINE_ALIASES.get(engine, engine)
        if kind != "stt" or engine not in STT_ENGINES:
            return await self._run_engine(kind, payload, request, engine)
        vocabulary = self._vocabulary_for(request)
        mode = whisper_hint_mode()
        hint = vocabulary.hint if vocabulary is not None and mode != "off" and engine == "whisper-small" else None
        result = await self._run_engine(kind, payload, request, engine, hint, mode if hint else None)
        if vocabulary is not None and stt_correction_enabled() and isinstance(result.get("text"), str):
            result = _correct_transcript(result, vocabulary)
            made = result["corrections"]
            if made:
                # engine is already one of STT_ENGINES (checked above): logged as is, hyphen included.
                logger.info("STT corrections engine=%s count=%d anchored=%d", engine, len(made),
                            sum(bool(c.get("anchored")) for c in made))
        return result

    async def _run_engine(self, kind, payload, request=None, engine="vosk", hint=None, hint_mode=None):
        if kind == "stt" and engine not in STT_ENGINES:
            raise KnowledgeError("voice_engine_invalid", "Select a supported local speech engine.", 422)
        if self.busy:
            raise KnowledgeError("voice_busy", "A local speech operation is already running. Try again after it finishes.", 409)
        if kind == "stt" and engine == "whisper-small":
            return await self._run_persistent_whisper(payload, request, hint, hint_mode)
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
