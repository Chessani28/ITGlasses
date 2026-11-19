"""Voz 3: Whisper large-v3-turbo on the GPU, Small on the CPU as the fallback, then Vosk in the app (503)."""
import asyncio
import json
import logging
import sys
import time

import pytest
from fastapi.testclient import TestClient

from backend.app.knowledge.models import KnowledgeError
from backend.app.main import create_app
from backend.app.providers.mock import MockLLM
from backend.app.voice import persistent_worker as pw
from backend.app.voice import service as voice_service_module
from backend.app.voice.persistent_worker import (WorkerCrashedError, WorkerGpuError, WorkerInferenceError,
                                                 WorkerStartupError)
from backend.app.voice.service import (VoiceService, whisper_abort_vram_mib, whisper_compute_type, whisper_device,
                                       whisper_idle_seconds, whisper_min_vram_mib, whisper_preload_enabled)
from test_voice import wav
from test_voice_whisper import FakePersistentWorker, prepare_whisper

GIB = 1024**3
VOZ3_VARIABLES = (
    "ITGLASSES_WHISPER_DEVICE", "ITGLASSES_WHISPER_COMPUTE_TYPE", "ITGLASSES_WHISPER_FALLBACK_MODEL",
    "ITGLASSES_WHISPER_PRELOAD", "ITGLASSES_WHISPER_MIN_VRAM_MIB", "ITGLASSES_WHISPER_ABORT_VRAM_MIB",
    "ITGLASSES_WHISPER_IDLE_SECONDS", "ITGLASSES_WHISPER_CUDA_DLLS",
)


@pytest.fixture(autouse=True)
def _clean_voz3_environment(monkeypatch):
    for name in VOZ3_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _model_folder(path, files):
    path.mkdir(parents=True)
    for name in files:
        (path / name).write_bytes(b"model")
    return path


TURBO_FILES = ("model.bin", "config.json", "tokenizer.json", "vocabulary.json", "preprocessor_config.json")


class GpuFakeWorker(FakePersistentWorker):
    def __init__(self, python, model, device="cpu", compute_type="int8", behavior=None, startup_error=None):
        super().__init__(python, model, behavior)
        self.runtime = (device, compute_type)
        self.startup_error = startup_error

    def start(self):
        if self.startup_error is not None:
            self.start_calls += 1
            raise self.startup_error
        return super().start()

    def transcribe(self, audio, hint=None, hint_mode=None):
        return super().transcribe(audio)


class Gpu:
    """VoiceService configured for turbo on CUDA with a Small fallback; fakes for workers, VRAM and RAM."""

    def __init__(self, tmp_path, monkeypatch, *, fallback=True, free_mib=4000, plan=()):
        self.python, self.small = prepare_whisper(tmp_path, monkeypatch)
        self.turbo = _model_folder(tmp_path / "faster-whisper-large-v3-turbo", TURBO_FILES).resolve()
        monkeypatch.setenv("ITGLASSES_WHISPER_MODEL", str(self.turbo))
        monkeypatch.setenv("ITGLASSES_WHISPER_DEVICE", "cuda")
        if fallback:
            monkeypatch.setenv("ITGLASSES_WHISPER_FALLBACK_MODEL", str(self.small))
        self.memory = {"free_mib": free_mib, "used_mib": 6144 - free_mib, "total_mib": 6144}
        self.queries = 0
        self.gpu_error = None
        self.ram = 16 * GIB
        self.workers = []
        self.plan = iter(plan)

        def query():
            self.queries += 1
            if self.gpu_error:
                raise self.gpu_error
            return dict(self.memory)

        def create(python, model, root, temporary, device="cpu", compute_type="int8"):
            startup_error, behavior = next(self.plan, (None, None))
            worker = GpuFakeWorker(python, model, device, compute_type, behavior, startup_error)
            self.workers.append(worker)
            return worker

        monkeypatch.setattr(voice_service_module, "query_gpu_memory", query)
        monkeypatch.setattr(voice_service_module, "_available_system_memory", lambda: self.ram)
        monkeypatch.setattr(voice_service_module, "PersistentWhisperWorker", create)
        self.service = VoiceService(tmp_path)

    def set_free(self, free_mib):
        self.memory["free_mib"] = free_mib
        self.service._vram_cache = None

    def run(self):
        return self.service.run("stt", wav(), engine="whisper-small")


def _oom(worker, _audio):
    worker.alive = False
    raise WorkerGpuError("cuda_out_of_memory")


def _crash(worker, _audio):
    worker.alive = False
    raise WorkerCrashedError("simulated worker exit")


# --- Model folders ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("files", "expected"), [
    (("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"), "vocabulary.txt"),
    (TURBO_FILES, "preprocessor_config.json"),
    (("model.bin", "config.json", "tokenizer.json", "vocabulary.json"), None),       # 80 bands instead of 128
    (("config.json", "tokenizer.json", "vocabulary.txt"), None),
    (("model.bin", "config.json", "vocabulary.txt"), None),
])
def test_model_folder_accepts_txt_or_json_with_preprocessor(files, expected, tmp_path):
    folder = _model_folder(tmp_path / "model", files)
    result = pw.model_files(folder)
    assert (result is None) if expected is None else (expected in result)
    assert pw.model_files(tmp_path / "missing") is None


def test_service_configuration_accepts_the_turbo_folder(tmp_path, monkeypatch):
    gpu = Gpu(tmp_path, monkeypatch)
    config, reason = VoiceService(tmp_path)._whisper_configuration()
    assert reason is None
    assert config["model"] == gpu.turbo and config["device"] == "cuda" and config["fallback"] == gpu.small
    (gpu.turbo / "preprocessor_config.json").unlink()
    config, reason = VoiceService(tmp_path)._whisper_configuration()
    assert config is None and "incomplete" in reason


# --- Settings --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("value", "expected"), [(None, "cpu"), ("", "cpu"), ("cuda", "cuda"), (" CUDA ", "cuda"),
                                                 ("gpu", "cpu"), ("cpu", "cpu")])
def test_device_setting(value, expected, monkeypatch):
    if value is not None:
        monkeypatch.setenv("ITGLASSES_WHISPER_DEVICE", value)
    assert whisper_device() == expected


@pytest.mark.parametrize(("device", "value", "expected"), [
    ("cpu", None, "int8"), ("cuda", None, "int8_float16"), ("cuda", "float16", "float16"), ("cuda", "int8", "int8"),
    ("cpu", "float16", "int8"), ("cuda", "float32", "int8_float16"), ("cpu", "INT8_FLOAT32", "int8_float32"),
])
def test_compute_type_setting(device, value, expected, monkeypatch):
    if value is not None:
        monkeypatch.setenv("ITGLASSES_WHISPER_COMPUTE_TYPE", value)
    assert whisper_compute_type(device) == expected


def test_vram_idle_and_preload_settings(monkeypatch):
    assert whisper_min_vram_mib() == 1000 and whisper_abort_vram_mib() == 256
    assert whisper_min_vram_mib("1500") == 1500 and whisper_min_vram_mib("0") == 0
    assert whisper_min_vram_mib("abc") == 1000 and whisper_min_vram_mib("-5") == 1000
    assert whisper_idle_seconds("cpu") == 60 and whisper_idle_seconds("cpu", "900") == 60
    assert whisper_idle_seconds("cuda") == 900 and whisper_idle_seconds("cuda", "1200") == 1200
    assert whisper_idle_seconds("cuda", "10") == 30 and whisper_idle_seconds("cuda", "x") == 900
    assert whisper_idle_seconds("cuda", "0") == 900
    assert whisper_preload_enabled() is False
    assert whisper_preload_enabled("true") is True and whisper_preload_enabled("off") is False
    assert whisper_preload_enabled("maybe") is False


# --- Worker side -----------------------------------------------------------------------------------------------

def test_worker_runtime_arguments_and_gpu_startup_timeout(tmp_path):
    cpu = pw.PersistentWhisperWorker(tmp_path / "python.exe", tmp_path / "model", tmp_path, tmp_path / "tmp")
    assert cpu.runtime == ("cpu", "int8") and cpu.startup_timeout == pw.MODEL_START_TIMEOUT_SECONDS
    assert cpu.command()[-4:] == ["--device", "cpu", "--compute-type", "int8"]
    gpu = pw.PersistentWhisperWorker(tmp_path / "python.exe", tmp_path / "model", tmp_path, tmp_path / "tmp",
                                     device="cuda")
    assert gpu.runtime == ("cuda", "int8_float16") and gpu.startup_timeout == pw.GPU_STARTUP_TIMEOUT_SECONDS
    assert gpu.command()[-4:] == ["--device", "cuda", "--compute-type", "int8_float16"]
    assert gpu.configuration == cpu.configuration  # the runtime is compared separately
    for device, compute_type in (("gpu", None), ("cpu", "float16"), ("cuda", "float32")):
        with pytest.raises(ValueError):
            pw.PersistentWhisperWorker(tmp_path / "p", tmp_path / "m", tmp_path, tmp_path / "t",
                                       device=device, compute_type=compute_type)


@pytest.mark.parametrize(("arguments", "error_type"), [
    (["--device", "gpu"], "InvalidRuntime"),
    (["--device", "cpu", "--compute-type", "float16"], "InvalidRuntime"),
    ([], "ModelUnavailable"),
])
def test_worker_main_rejects_invalid_runtime_and_incomplete_models(arguments, error_type, tmp_path, monkeypatch,
                                                                   capsysbinary):
    model = _model_folder(tmp_path / "turbo", TURBO_FILES[:-1])  # no preprocessor_config.json
    monkeypatch.setattr(sys, "argv", ["persistent_worker", "--model", str(model), *arguments])
    assert pw.main() == 1
    response = json.loads(capsysbinary.readouterr().out.decode().splitlines()[-1])
    assert response["event"] == "startup_error" and response["error_type"] == error_type
    assert str(tmp_path) not in response["message"]


def test_cuda_dll_folders_come_from_the_worker_environment(tmp_path, monkeypatch):
    prefix = tmp_path / "env"
    for name in ("cudnn", "cublas"):
        (prefix / "Lib" / "site-packages" / "nvidia" / name / "bin").mkdir(parents=True)
    (prefix / "Lib" / "site-packages" / "nvidia" / "no-bin").mkdir()
    extra = tmp_path / "extra-dlls"
    extra.mkdir()
    found = pw.cuda_dll_directories(prefix, extra=f"relative\\dlls;{extra};{tmp_path / 'missing'}")
    names = [folder.parent.name for folder in found[:2]]
    assert names == ["cublas", "cudnn"] and found[2] == extra and len(found) == 3
    assert pw.cuda_dll_directories(tmp_path / "empty", extra="") == []

    added = []
    monkeypatch.setattr(pw.os, "add_dll_directory", added.append, raising=False)
    monkeypatch.setenv("PATH", "original")
    assert pw.enable_cuda_dlls(prefix, extra="") == 2
    assert len(added) == 2 and pw.os.environ["PATH"].endswith("original")
    assert str(found[0]) in pw.os.environ["PATH"]
    with pytest.raises(pw.CudaLibrariesMissing):
        pw.enable_cuda_dlls(tmp_path / "empty", extra="")


def test_gpu_errors_are_classified_and_mapped():
    assert pw._error_type(RuntimeError("CUDA failed with error out of memory"), "cuda") == "CudaOutOfMemory"
    assert pw._error_type(RuntimeError("CUDA failed with error out of memory"), "cpu") == "RuntimeError"
    assert pw._error_type(OSError("Could not load library cudnn_ops64_9.dll"), "cuda") == "CudaLibrariesMissing"
    assert pw._error_type(pw.CudaDeviceMissing("none"), "cuda") == "CudaDeviceMissing"
    assert pw._error_type(ValueError("bad audio"), "cuda") == "ValueError"
    failure = pw.inference_failure({"ok": False, "error_type": "CudaOutOfMemory"})
    assert isinstance(failure, WorkerGpuError) and failure.code == "voice_vram_limit"
    assert isinstance(pw.inference_failure({"ok": False, "error_type": "ValueError"}), WorkerInferenceError)
    startup = pw.startup_failure({"event": "startup_error", "error_type": "CudaLibrariesMissing"})
    assert isinstance(startup, WorkerGpuError) and startup.code == "voice_gpu_unavailable"
    assert pw.startup_failure({"event": "startup_error", "error_type": "CudaDeviceMissing"}).code == \
        "voice_gpu_unavailable"
    generic = pw.startup_failure({"event": "startup_error", "error_type": "ModelUnavailable"})
    assert isinstance(generic, WorkerStartupError) and not isinstance(generic, WorkerGpuError)


# --- Fallback chain --------------------------------------------------------------------------------------------

def test_turbo_on_the_gpu_transcribes_and_reports_model_and_device(tmp_path, monkeypatch):
    gpu = Gpu(tmp_path, monkeypatch)

    async def exercise():
        result = await gpu.run()
        again = await gpu.run()
        await gpu.service.shutdown()
        return result, again

    result, again = asyncio.run(exercise())
    assert result["engine"] == "whisper-small"  # the app keeps seeing the same engine
    assert (result["whisper_model"], result["device"], result["compute_type"]) == \
        ("large-v3-turbo", "cuda", "int8_float16")
    assert result["fallback_reason"] is None and again["fallback_reason"] is None
    assert len(gpu.workers) == 1 and gpu.workers[0].runtime == ("cuda", "int8_float16")
    assert gpu.workers[0].configuration[1] == str(gpu.turbo)
    assert gpu.queries == 1  # a loaded GPU worker is used without measuring again


def test_low_vram_uses_small_on_the_cpu_without_pausing_the_gpu(tmp_path, monkeypatch):
    gpu = Gpu(tmp_path, monkeypatch, free_mib=800)

    async def exercise():
        low = await gpu.run()
        gpu.set_free(1249)  # with Ollama loaded about 1249 MiB stay free: enough for the 1000 MiB default
        back = await gpu.run()
        await gpu.service.shutdown()
        return low, back

    low, back = asyncio.run(exercise())
    assert (low["whisper_model"], low["device"], low["fallback_reason"]) == \
        ("whisper-small", "cpu", "voice_vram_limit")
    assert gpu.workers[0].runtime == ("cpu", "int8")
    assert not gpu.service._gpu_cooldown_active()
    assert back["device"] == "cuda" and back["fallback_reason"] is None
    assert gpu.workers[0].close_calls == 1  # Small was unloaded: one worker at a time


def test_low_vram_without_fallback_answers_503(tmp_path, monkeypatch):
    gpu = Gpu(tmp_path, monkeypatch, fallback=False, free_mib=800)
    with pytest.raises(KnowledgeError) as error:
        asyncio.run(gpu.run())
    assert (error.value.code, error.value.status) == ("voice_vram_limit", 503)
    assert gpu.workers == [] and not gpu.service.busy


def test_unreadable_gpu_pauses_it_for_five_minutes(tmp_path, monkeypatch):
    gpu = Gpu(tmp_path, monkeypatch)
    gpu.gpu_error = OSError("nvidia-smi failed")

    async def exercise():
        first = await gpu.run()
        gpu.gpu_error = None
        gpu.set_free(4000)
        paused = await gpu.run()
        assert 290 < gpu.service._gpu_cooldown_until - time.monotonic() <= 300
        gpu.service._gpu_cooldown_until = 0.0  # five minutes later
        resumed = await gpu.run()
        await gpu.service.shutdown()
        return first, paused, resumed

    first, paused, resumed = asyncio.run(exercise())
    assert first["fallback_reason"] == "voice_gpu_unavailable" == paused["fallback_reason"]
    assert gpu.queries == 2  # nothing is measured while the GPU is paused
    assert resumed["device"] == "cuda" and resumed["fallback_reason"] is None


@pytest.mark.parametrize(("startup_error", "reason"), [
    (WorkerGpuError("cuda_libraries_missing"), "voice_gpu_unavailable"),
    (WorkerGpuError("cuda_out_of_memory"), "voice_vram_limit"),
    (WorkerStartupError("startup failed"), "voice_worker_startup"),
    (WorkerCrashedError("closed its pipe"), "voice_worker_startup"),
])
def test_gpu_startup_failure_falls_back_once_and_pauses_the_gpu(startup_error, reason, tmp_path, monkeypatch):
    gpu = Gpu(tmp_path, monkeypatch, plan=[(startup_error, None)])
    result = asyncio.run(gpu.run())
    assert result["device"] == "cpu" and result["fallback_reason"] == reason
    assert [worker.runtime[0] for worker in gpu.workers] == ["cuda", "cpu"]
    assert gpu.service._gpu_cooldown_active()
    asyncio.run(gpu.service.shutdown())


@pytest.mark.parametrize(("behavior", "reason"), [(_oom, "voice_vram_limit"), (_crash, "voice_gpu_unavailable")])
def test_gpu_failure_while_transcribing_retries_the_request_once_on_the_cpu(behavior, reason, tmp_path,
                                                                            monkeypatch):
    gpu = Gpu(tmp_path, monkeypatch, plan=[(None, behavior)])
    result = asyncio.run(gpu.run())
    assert result["device"] == "cpu" and result["fallback_reason"] == reason
    assert gpu.workers[0].terminate_calls == 1 and gpu.workers[1].transcribe_calls == 1
    assert gpu.service._gpu_cooldown_active()
    asyncio.run(gpu.service.shutdown())


def test_gpu_failure_without_fallback_answers_503_for_the_app_to_use_vosk(tmp_path, monkeypatch):
    gpu = Gpu(tmp_path, monkeypatch, fallback=False, plan=[(None, _oom)])
    with pytest.raises(KnowledgeError) as error:
        asyncio.run(gpu.run())
    assert (error.value.code, error.value.status) == ("voice_vram_limit", 503)


def test_a_failing_fallback_is_reported_without_a_third_attempt(tmp_path, monkeypatch):
    gpu = Gpu(tmp_path, monkeypatch, plan=[(None, _oom), (None, _crash)])
    with pytest.raises(KnowledgeError) as error:
        asyncio.run(gpu.run())
    assert error.value.code == "voice_inference_failed"
    assert len(gpu.workers) == 2


def test_cpu_device_never_uses_the_fallback(tmp_path, monkeypatch):
    gpu = Gpu(tmp_path, monkeypatch, plan=[(None, _crash)])
    monkeypatch.setenv("ITGLASSES_WHISPER_DEVICE", "cpu")
    monkeypatch.setenv("ITGLASSES_WHISPER_MODEL", str(gpu.small))
    with pytest.raises(KnowledgeError) as error:
        asyncio.run(gpu.run())
    assert error.value.code == "voice_inference_failed" and len(gpu.workers) == 1
    assert gpu.queries == 0  # the CPU never asks nvidia-smi


def test_low_ram_on_the_gpu_does_not_load_the_fallback(tmp_path, monkeypatch):
    gpu = Gpu(tmp_path, monkeypatch)
    gpu.ram = 2 * GIB
    with pytest.raises(KnowledgeError) as error:
        asyncio.run(gpu.run())
    assert error.value.code == "voice_resource_limit" and gpu.workers == []


def test_compute_type_change_restarts_the_worker(tmp_path, monkeypatch):
    gpu = Gpu(tmp_path, monkeypatch)

    async def exercise():
        await gpu.run()
        monkeypatch.setenv("ITGLASSES_WHISPER_COMPUTE_TYPE", "float16")
        result = await gpu.run()
        await gpu.service.shutdown()
        return result

    result = asyncio.run(exercise())
    assert [worker.runtime for worker in gpu.workers] == [("cuda", "int8_float16"), ("cuda", "float16")]
    assert gpu.workers[0].close_calls == 1 and result["compute_type"] == "float16"


# --- Keeping it loaded -----------------------------------------------------------------------------------------

def test_chat_and_speech_synthesis_keep_the_gpu_whisper(tmp_path, monkeypatch):
    gpu = Gpu(tmp_path, monkeypatch)

    async def exercise():
        await gpu.run()
        gpu.ram = 4 * GIB  # below the 8 GiB keep threshold of a CPU Whisper
        await gpu.service.release_whisper()
        assert gpu.workers[0].alive and gpu.workers[0].close_calls == 0
        await gpu.service.shutdown()
        assert gpu.workers[0].close_calls == 1  # shutdown always unloads

    asyncio.run(exercise())


def test_gpu_whisper_idle_time_is_configurable(tmp_path, monkeypatch):
    gpu = Gpu(tmp_path, monkeypatch)

    async def exercise():
        await gpu.run()
        gpu.workers[0].last_used = time.monotonic() - 61  # 60 s would unload a CPU Whisper
        await asyncio.sleep(0.35)
        assert gpu.workers[0].alive
        await gpu.service.shutdown()
        monkeypatch.setenv("ITGLASSES_WHISPER_IDLE_SECONDS", "30")
        await gpu.run()
        gpu.workers[1].last_used = time.monotonic() - 31
        await asyncio.sleep(0.35)
        assert gpu.workers[1].close_calls == 1

    asyncio.run(exercise())


def test_idle_gpu_whisper_unloads_when_free_vram_runs_out(tmp_path, monkeypatch):
    gpu = Gpu(tmp_path, monkeypatch)
    monkeypatch.setattr(voice_service_module, "VRAM_IDLE_CHECK_SECONDS", 0)
    monkeypatch.setattr(voice_service_module, "VRAM_CACHE_SECONDS", 0)

    async def exercise():
        await gpu.run()
        await asyncio.sleep(0.35)
        assert gpu.workers[0].alive  # 4000 MiB free
        gpu.set_free(200)
        await asyncio.sleep(0.35)
        assert gpu.workers[0].close_calls == 1

    asyncio.run(exercise())


def test_low_ram_still_unloads_the_gpu_whisper(tmp_path, monkeypatch):
    gpu = Gpu(tmp_path, monkeypatch)

    async def exercise():
        await gpu.run()
        gpu.ram = 1 * GIB
        await asyncio.sleep(0.35)
        assert gpu.workers[0].terminate_calls == 1

    asyncio.run(exercise())


# --- Preload ---------------------------------------------------------------------------------------------------

def test_preload_loads_turbo_and_then_serves_the_first_question(tmp_path, monkeypatch):
    gpu = Gpu(tmp_path, monkeypatch)

    async def exercise():
        assert await gpu.service.preload_whisper() is True
        assert gpu.workers[0].alive and not gpu.service.busy
        result = await gpu.run()
        await gpu.service.shutdown()
        return result

    result = asyncio.run(exercise())
    assert len(gpu.workers) == 1 and result["worker_startup_seconds"] == 0


@pytest.mark.parametrize("problem", ["vram", "startup", "unconfigured"])
def test_preload_never_loads_the_fallback_and_never_raises(problem, tmp_path, monkeypatch):
    plan = [(WorkerGpuError("cuda_libraries_missing"), None)] if problem == "startup" else []
    gpu = Gpu(tmp_path, monkeypatch, free_mib=500 if problem == "vram" else 4000, plan=plan)
    if problem == "unconfigured":
        monkeypatch.delenv("ITGLASSES_WHISPER_MODEL")
    assert asyncio.run(gpu.service.preload_whisper()) is False
    assert [worker.runtime[0] for worker in gpu.workers] == (["cuda"] if problem == "startup" else [])
    assert gpu.service._gpu_cooldown_active() is (problem == "startup")
    assert not gpu.service.busy


def test_backend_startup_preloads_only_when_enabled(monkeypatch):
    class VoiceSpy:
        def __init__(self):
            self.preloads = 0

        async def preload_whisper(self):
            self.preloads += 1
            return True

        async def shutdown(self):
            pass

    voice = VoiceSpy()
    with TestClient(create_app(MockLLM(), voice=voice)):
        pass
    assert voice.preloads == 0
    monkeypatch.setenv("ITGLASSES_WHISPER_PRELOAD", "true")
    with TestClient(create_app(MockLLM(), voice=voice)):
        pass
    assert voice.preloads == 1


# --- Header, status and logs -----------------------------------------------------------------------------------

def test_whisper_header_is_accepted_with_whisper_small_as_its_synonym(tmp_path):
    class VoiceSpy:
        def __init__(self):
            self.engines = []

        async def run(self, kind, payload, request=None, engine="vosk"):
            self.engines.append(engine)
            return {"text": "ok", "engine": "whisper-small" if engine != "vosk" else "vosk"}

    voice = VoiceSpy()
    with TestClient(create_app(MockLLM(), voice=voice)) as client:
        codes = [client.post("/api/v1/voice/transcribe", content=wav(),
                             headers={"Content-Type": "audio/wav", "X-ITGlasses-STT-Engine": engine}).status_code
                 for engine in ("whisper", "Whisper-Small", "vosk", "whisper-large")]
    assert codes == [200, 200, 200, 422]
    assert voice.engines == ["whisper-small", "whisper-small", "vosk"]


def test_service_accepts_the_whisper_alias(tmp_path, monkeypatch):
    gpu = Gpu(tmp_path, monkeypatch)
    result = asyncio.run(gpu.service.run("stt", wav(), engine="whisper"))
    assert result["engine"] == "whisper-small" and result["device"] == "cuda"
    asyncio.run(gpu.service.shutdown())


def test_status_reports_model_device_and_fallback_without_paths(tmp_path, monkeypatch):
    gpu = Gpu(tmp_path, monkeypatch)
    whisper = gpu.service.status()["stt_engines"]["whisper-small"]
    assert whisper["available"] is True
    assert (whisper["model"], whisper["device"], whisper["compute_type"]) == ("large-v3-turbo", "cuda", "int8_float16")
    assert whisper["fallback_available"] is True and whisper["fallback_model"] == "whisper-small"
    assert whisper["gpu_paused"] is False
    assert str(tmp_path) not in json.dumps(gpu.service.status())
    assert gpu.queries == 0  # status never runs nvidia-smi


def test_logs_carry_codes_and_model_names_only(tmp_path, monkeypatch, caplog):
    gpu = Gpu(tmp_path, monkeypatch, plan=[(WorkerGpuError("cuda_libraries_missing"), None)])
    with caplog.at_level(logging.INFO, logger="backend.app.voice.service"):
        asyncio.run(gpu.run())
        asyncio.run(gpu.service.shutdown())
    assert "voice_gpu_unavailable" in caplog.text and "model=whisper-small" in caplog.text
    assert str(tmp_path) not in caplog.text and "transcript" not in caplog.text
