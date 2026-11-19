"""Owned, bounded local Whisper worker process for Windows."""

import base64
import ctypes
import json
import os
from pathlib import Path
import queue
import gc
import io
import argparse
import sys
import subprocess
import threading
import time
import wave
from ctypes import wintypes

MAX_REQUEST_BYTES = 1_400_000
# Voz 2: optional vocabulary hint for Whisper (built by the backend from authorized titles and headings).
HINT_MODES = ("hotwords", "initial_prompt")
MAX_HINT_CHARS = 1000
# A hinted transcript made only of hint words, on audio Whisper itself rates as no speech, is an echo.
HINT_ECHO_NO_SPEECH = 0.6
MAX_RESPONSE_BYTES = 256_000
MIN_LOAD_RAM_BYTES = 3 * 1024**3
ABORT_RAM_BYTES = int(1.5 * 1024**3)
MODEL_START_TIMEOUT_SECONDS = 120
REQUEST_TIMEOUT_SECONDS = 90
POLL_SECONDS = 0.1
# Voz 3: Whisper on the GPU (CUDA). Compute types allowed per device; the first one is the default (the
# configurations measured with work/voice-hints-eval.py: int8 on the CPU, int8_float16 on the RTX 2060).
WHISPER_DEVICES = ("cpu", "cuda")
COMPUTE_TYPES = {"cpu": ("int8", "int8_float32", "float32"), "cuda": ("int8_float16", "float16", "int8")}
# A GPU load measured 3.5 s alone and 11.5 s with Ollama loaded; a shorter limit than on the CPU leaves time for
# the CPU fallback inside the app's 120 s read timeout.
GPU_STARTUP_TIMEOUT_SECONDS = 45
# Process memory after loading: the CPU model lives in RAM, the CUDA weights live in VRAM.
MIN_LOADED_PRIVATE_BYTES = {"cpu": 500 * 1024**2, "cuda": 100 * 1024**2}
# Worker error types that describe the GPU, mapped to the reasons the backend acts on.
GPU_ERROR_TYPES = {
    "CudaLibrariesMissing": "cuda_libraries_missing",
    "CudaDeviceMissing": "cuda_device_missing",
    "CudaOutOfMemory": "cuda_out_of_memory",
}
_DLL_DIRECTORY_HANDLES = []

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_TH32CS_SNAPPROCESS = 0x00000002
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_QUERY_INFORMATION = 0x0400
_SYNCHRONIZE = 0x00100000
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_ulong),
        ("memory_load", ctypes.c_ulong),
        ("total_physical", ctypes.c_ulonglong),
        ("available_physical", ctypes.c_ulonglong),
        ("total_page_file", ctypes.c_ulonglong),
        ("available_page_file", ctypes.c_ulonglong),
        ("total_virtual", ctypes.c_ulonglong),
        ("available_virtual", ctypes.c_ulonglong),
        ("available_extended_virtual", ctypes.c_ulonglong),
    ]


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("page_fault_count", ctypes.c_ulong),
        ("peak_working_set", ctypes.c_size_t),
        ("working_set", ctypes.c_size_t),
        ("quota_peak_paged_pool", ctypes.c_size_t),
        ("quota_paged_pool", ctypes.c_size_t),
        ("quota_peak_nonpaged_pool", ctypes.c_size_t),
        ("quota_nonpaged_pool", ctypes.c_size_t),
        ("pagefile_usage", ctypes.c_size_t),
        ("peak_pagefile_usage", ctypes.c_size_t),
        ("private_usage", ctypes.c_size_t),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            "read_operation_count",
            "write_operation_count",
            "other_operation_count",
            "read_transfer_count",
            "write_transfer_count",
            "other_transfer_count",
        )
    ]


class _JobBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("per_process_user_time_limit", ctypes.c_longlong),
        ("per_job_user_time_limit", ctypes.c_longlong),
        ("limit_flags", wintypes.DWORD),
        ("minimum_working_set_size", ctypes.c_size_t),
        ("maximum_working_set_size", ctypes.c_size_t),
        ("active_process_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority_class", wintypes.DWORD),
        ("scheduling_class", wintypes.DWORD),
    ]


class _JobExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("basic_limit_information", _JobBasicLimitInformation),
        ("io_info", _IoCounters),
        ("process_memory_limit", ctypes.c_size_t),
        ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory_used", ctypes.c_size_t),
        ("peak_job_memory_used", ctypes.c_size_t),
    ]


class _ProcessEntry32(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("usage", wintypes.DWORD),
        ("process_id", wintypes.DWORD),
        ("default_heap_id", ctypes.c_size_t),
        ("module_id", wintypes.DWORD),
        ("thread_count", wintypes.DWORD),
        ("parent_process_id", wintypes.DWORD),
        ("base_priority", wintypes.LONG),
        ("flags", wintypes.DWORD),
        ("executable", ctypes.c_wchar * 260),
    ]


if os.name == "nt":
    _kernel = ctypes.windll.kernel32
    _psapi = ctypes.windll.psapi
    _kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(_MemoryStatus)]
    _kernel.GlobalMemoryStatusEx.restype = wintypes.BOOL
    _kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _kernel.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _kernel.SetInformationJobObject.restype = wintypes.BOOL
    _kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel.TerminateJobObject.restype = wintypes.BOOL
    _kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel.OpenProcess.restype = wintypes.HANDLE
    _kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel.CloseHandle.restype = wintypes.BOOL
    _kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel.Process32FirstW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessEntry32),
    ]
    _kernel.Process32FirstW.restype = wintypes.BOOL
    _kernel.Process32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessEntry32),
    ]
    _kernel.Process32NextW.restype = wintypes.BOOL
    _kernel.IsProcessInJob.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.BOOL),
    ]
    _kernel.IsProcessInJob.restype = wintypes.BOOL
    _psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    _psapi.GetProcessMemoryInfo.restype = wintypes.BOOL


class PersistentWorkerError(RuntimeError):
    pass


class WorkerStartupError(PersistentWorkerError):
    pass


class WorkerCrashedError(PersistentWorkerError):
    pass


class WorkerProtocolError(PersistentWorkerError):
    pass


class WorkerInferenceError(PersistentWorkerError):
    pass


class WorkerMemoryLimitError(PersistentWorkerError):
    pass


class WorkerResourceMonitorError(PersistentWorkerError):
    pass


class WorkerTimeoutError(PersistentWorkerError):
    pass


class WorkerGpuError(PersistentWorkerError):
    """GPU failure: CUDA libraries or device missing, or GPU memory exhausted. ``code`` is the API error code."""

    CODES = {
        "cuda_libraries_missing": "voice_gpu_unavailable",
        "cuda_device_missing": "voice_gpu_unavailable",
        "cuda_out_of_memory": "voice_vram_limit",
    }

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason

    @property
    def code(self):
        return self.CODES.get(self.reason, "voice_gpu_unavailable")


class CudaLibrariesMissing(RuntimeError):
    pass


class CudaDeviceMissing(RuntimeError):
    pass


def model_files(model_path):
    """Required file names of a CTranslate2 Whisper folder, or None when it is incomplete.

    model.bin, config.json and tokenizer.json, plus vocabulary.txt (Small) or vocabulary.json together with
    preprocessor_config.json (large-v3 / turbo: without it faster-whisper would use 80 mel bands instead of 128 and
    transcribe garbage without any error).
    """
    path = Path(model_path)
    base = ("model.bin", "config.json", "tokenizer.json")
    if not path.is_dir() or not all((path / name).is_file() for name in base):
        return None
    if (path / "vocabulary.txt").is_file():
        return base + ("vocabulary.txt",)
    if (path / "vocabulary.json").is_file() and (path / "preprocessor_config.json").is_file():
        return base + ("vocabulary.json", "preprocessor_config.json")
    return None


def validate_runtime(device, compute_type=None):
    """(device, compute type) after validation; the compute type defaults to the device's first allowed one."""
    if device not in WHISPER_DEVICES:
        raise ValueError("Whisper device must be cpu or cuda")
    compute_type = compute_type or COMPUTE_TYPES[device][0]
    if compute_type not in COMPUTE_TYPES[device]:
        raise ValueError("Whisper compute type is not valid for this device")
    return device, compute_type


def cuda_dll_directories(prefix=None, extra=None):
    """Folders with the cuBLAS and cuDNN DLLs for the worker environment.

    <environment>\\Lib\\site-packages\\nvidia\\*\\bin (the pip wheels nvidia-cublas-cu12 / nvidia-cudnn-cu12), only
    inside the environment, plus the absolute folders in ITGLASSES_WHISPER_CUDA_DLLS (separated by ";").
    """
    prefix = Path(sys.prefix if prefix is None else prefix).resolve()
    found = []
    for folder in sorted((prefix / "Lib" / "site-packages" / "nvidia").glob("*/bin")):
        try:
            resolved = folder.resolve()
            resolved.relative_to(prefix)
        except (OSError, ValueError):
            continue
        if resolved.is_dir():
            found.append(resolved)
    extra = os.environ.get("ITGLASSES_WHISPER_CUDA_DLLS", "") if extra is None else extra
    for item in str(extra or "").split(";"):
        item = item.strip()
        if item and Path(item).is_absolute() and Path(item).is_dir() and Path(item) not in found:
            found.append(Path(item))
    return found


def enable_cuda_dlls(prefix=None, extra=None):
    """Make the CUDA DLLs loadable in this process only (add_dll_directory and this process's PATH).

    cuDNN 9 loads its sub-libraries by name, so the PATH is needed as well. The system PATH is never changed.
    """
    folders = cuda_dll_directories(prefix, extra)
    if not folders:
        raise CudaLibrariesMissing("cuBLAS and cuDNN were not found in the Whisper environment")
    if hasattr(os, "add_dll_directory"):
        for folder in folders:
            _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(folder)))
    os.environ["PATH"] = os.pathsep.join([*(str(folder) for folder in folders), os.environ.get("PATH", "")])
    return len(folders)


def _error_type(error, device):
    """Error type reported by the worker; on CUDA, GPU memory and library failures get their own names."""
    name = type(error).__name__
    if device == "cuda" and name not in GPU_ERROR_TYPES:
        text = str(error).lower()
        if "out of memory" in text:
            return "CudaOutOfMemory"
        if any(marker in text for marker in ("cublas", "cudnn", "cudart", ".dll")):
            return "CudaLibrariesMissing"
    return name


def startup_failure(response):
    """Exception for a worker startup_error response."""
    error_type = response.get("error_type")
    if error_type in GPU_ERROR_TYPES:
        return WorkerGpuError(GPU_ERROR_TYPES[error_type])
    return WorkerStartupError(response.get("message", "Whisper startup failed"))


def inference_failure(response):
    """Exception for a failed transcription response (ok false)."""
    error_type = response.get("error_type")
    if error_type in GPU_ERROR_TYPES:
        return WorkerGpuError(GPU_ERROR_TYPES[error_type])
    return WorkerInferenceError(response.get("message", "Whisper inference failed"))


def available_system_memory():
    if os.name != "nt":
        raise OSError("the persistent Whisper worker requires Windows")
    status = _MemoryStatus()
    status.length = ctypes.sizeof(status)
    if not _kernel.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise ctypes.WinError()
    return status.available_physical


def process_memory(handle):
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not _psapi.GetProcessMemoryInfo(
        handle, ctypes.byref(counters), counters.cb
    ):
        raise ctypes.WinError()
    return counters.working_set, counters.private_usage


def process_snapshot():
    snapshot = _kernel.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot == _INVALID_HANDLE_VALUE:
        raise ctypes.WinError()
    try:
        entry = _ProcessEntry32()
        entry.size = ctypes.sizeof(entry)
        found = _kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        if not found:
            raise ctypes.WinError()
        processes = []
        while found:
            processes.append(
                (entry.process_id, entry.parent_process_id, entry.executable)
            )
            found = _kernel.Process32NextW(snapshot, ctypes.byref(entry))
        return processes
    finally:
        _kernel.CloseHandle(snapshot)


def _descendants(parent_pid, processes):
    descendants = set()
    changed = True
    while changed:
        changed = False
        for pid, parent, _ in processes:
            if (parent == parent_pid or parent in descendants) and pid not in descendants:
                descendants.add(pid)
                changed = True
    return descendants


def _open_process(pid, access):
    handle = _kernel.OpenProcess(access, False, pid)
    if not handle:
        raise ctypes.WinError()
    return handle


def _is_in_job(pid, job):
    handle = _open_process(pid, _PROCESS_QUERY_INFORMATION)
    try:
        result = wintypes.BOOL()
        if not _kernel.IsProcessInJob(handle, job, ctypes.byref(result)):
            raise ctypes.WinError()
        return bool(result.value)
    finally:
        _kernel.CloseHandle(handle)


def _assign_to_job(pid, job):
    handle = _open_process(
        pid,
        _PROCESS_SET_QUOTA | _PROCESS_TERMINATE | _PROCESS_QUERY_INFORMATION,
    )
    try:
        if not _kernel.AssignProcessToJobObject(job, handle):
            raise ctypes.WinError()
    finally:
        _kernel.CloseHandle(handle)


def _write_all(pipe, data):
    view = memoryview(data)
    while view:
        written = pipe.write(view)
        if not written:
            raise BrokenPipeError("Whisper worker request pipe closed")
        view = view[written:]
    pipe.flush()


def _create_kill_on_close_job():
    job = _kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError()
    info = _JobExtendedLimitInformation()
    info.basic_limit_information.limit_flags = 0x00002000
    if not _kernel.SetInformationJobObject(
        job,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        error = ctypes.WinError()
        _kernel.CloseHandle(job)
        raise error
    return job


def validate_audio(raw):
    import io
    import wave

    if not raw or len(raw) > 960_044:
        raise ValueError("audio exceeds the 30-second size limit")
    with wave.open(io.BytesIO(raw), "rb") as audio:
        if (
            audio.getnchannels(),
            audio.getsampwidth(),
            audio.getframerate(),
            audio.getcomptype(),
        ) != (1, 2, 16000, "NONE"):
            raise ValueError("audio must be mono 16-bit PCM at 16 kHz")
        frames = audio.getnframes()
        if not 1600 <= frames <= 480000:
            raise ValueError("audio duration must be between 0.1 and 30 seconds")
        if len(audio.readframes(frames)) != frames * 2:
            raise ValueError("audio is incomplete")


def _send_response(response):
    sys.stdout.buffer.write(
        json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        + b"\n"
    )
    sys.stdout.buffer.flush()


def _warm_up(model):
    """One short pass on CUDA so missing cuDNN/cuBLAS or GPU memory fail at startup, not on the first question."""
    import numpy as np

    segments, _ = model.transcribe(
        np.zeros(16000, dtype=np.float32),
        language="es",
        beam_size=1,
        condition_on_previous_text=False,
        vad_filter=False,
    )
    for _ in segments:
        pass


def _load_model(model_path, device="cpu", compute_type="int8"):
    if available_system_memory() < MIN_LOAD_RAM_BYTES:
        raise WorkerMemoryLimitError(
            "Whisper Small requires at least 3 GiB of available system RAM"
        )
    import av
    import faster_whisper.audio as faster_whisper_audio
    from faster_whisper import WhisperModel

    if int(av.__version__.split(".", 1)[0]) >= 19:
        original_open = av.open

        def compatible_open(*args, **kwargs):
            kwargs.pop("metadata_errors", None)
            return original_open(*args, **kwargs)

        faster_whisper_audio.av.open = compatible_open
    if device == "cuda":
        import ctranslate2

        if ctranslate2.get_cuda_device_count() < 1:
            raise CudaDeviceMissing("no CUDA device is visible to CTranslate2")
    model = WhisperModel(
        str(model_path),
        device=device,
        compute_type=compute_type,
        cpu_threads=4,
        num_workers=1,
        local_files_only=True,
    )
    if device == "cuda":
        _warm_up(model)
    return model


def _fold_words(text):
    import unicodedata

    text = unicodedata.normalize("NFKD", (text or "").casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return "".join(c if c.isalnum() else " " for c in text).split()


def _hint_echo(text, hint, segments):
    """True when a hinted transcript only repeats hint words over audio rated as no speech (hallucination)."""
    words = _fold_words(text)
    if not hint or not words or not segments:
        return False
    if not all((segment.get("no_speech_prob") or 0) >= HINT_ECHO_NO_SPEECH for segment in segments):
        return False
    return set(words) <= set(_fold_words(hint))


def _validated_hint(request):
    """(hint, mode) from a request, or (None, None). Unknown modes or oversized hints are protocol errors."""
    hint, mode = request.get("hint"), request.get("hint_mode")
    if hint is None and mode is None:
        return None, None
    if not isinstance(hint, str) or not hint.strip() or len(hint) > MAX_HINT_CHARS or mode not in HINT_MODES:
        raise ValueError("invalid hint")
    return hint.strip(), mode


def _transcribe(model, request_id, raw, hint=None, hint_mode=None):
    import numpy as np

    cpu_started = time.process_time()
    validate_audio(raw)
    with wave.open(io.BytesIO(raw), "rb") as audio:
        pcm = audio.readframes(audio.getnframes())
    from .transcription import audio_quality

    quality = audio_quality(pcm)
    quality["tail_rms"] = audio_quality(pcm[-6400:] if len(pcm) >= 6400 else pcm)[
        "rms"
    ]
    if quality["silence"]:
        text = ""
        segments = []
        language_probability = None
        inference_seconds = 0.0
    else:
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        inference_started = time.perf_counter()
        options = {hint_mode: hint} if hint and hint_mode in HINT_MODES else {}
        segment_iter, info = model.transcribe(
            samples,
            language="es",
            task="transcribe",
            beam_size=5,
            best_of=5,
            temperature=0.0,
            condition_on_previous_text=False,
            vad_filter=False,
            word_timestamps=False,
            **options,
        )
        segment_results = []
        parts = []
        for segment in segment_iter:
            parts.append(segment.text)
            segment_results.append(
                {
                    "start": segment.start,
                    "end": segment.end,
                    "text": segment.text,
                    "avg_logprob": segment.avg_logprob,
                    "no_speech_prob": segment.no_speech_prob,
                    "compression_ratio": segment.compression_ratio,
                }
            )
        text = "".join(parts).strip()
        segments = segment_results
        if options and _hint_echo(text, hint, segments):
            text = ""  # Hint words over silence or noise: treat as "No se reconoció ninguna voz."
        language_probability = info.language_probability
        inference_seconds = time.perf_counter() - inference_started
    hint_used = bool(hint) and not quality["silence"]
    return {
        "id": request_id,
        "ok": True,
        "text": text,
        "raw_text": text,
        "normalized_text": text,
        "engine": "whisper-small",
        "language": "es",
        "language_probability": language_probability,
        "segments": segments,
        "inference_seconds": round(inference_seconds, 4),
        "cpu_seconds": round(time.process_time() - cpu_started, 3),
        "hint_used": hint_used,
        "hint_mode": hint_mode if hint_used else None,
        "audio_quality": quality,
        "needs_clarification": not bool(text),
        "quality_reasons": ["no_speech"] if not text else [],
        "clarification": (
            "No pude reconocer la pregunta con suficiente claridad. Repítela completa o escríbela."
            if not text
            else None
        ),
    }


def _worker_main(model_path, device="cpu", compute_type="int8"):
    try:
        model_started = time.perf_counter()
        model = _load_model(model_path, device, compute_type)
        ready = {
            "event": "ready",
            "pid": os.getpid(),
            "model_load_seconds": round(time.perf_counter() - model_started, 4),
            "device": device,
            "compute_type": compute_type,
        }
        if device == "cuda":
            import ctranslate2

            ready["cuda_devices"] = ctranslate2.get_cuda_device_count()
        _send_response(ready)
    except Exception as error:
        _send_response(
            {
                "event": "startup_error",
                "error_type": _error_type(error, device),
                "message": str(error)[:200],
            }
        )
        return 1

    while True:
        line = sys.stdin.buffer.readline(MAX_REQUEST_BYTES + 1)
        if not line:
            return 0
        if len(line) > MAX_REQUEST_BYTES or not line.endswith(b"\n"):
            _send_response(
                {
                    "event": "protocol_error",
                    "error_type": "RequestTooLarge",
                    "message": "Request exceeds the protocol size limit",
                }
            )
            return 1
        request = {}
        try:
            request = json.loads(line)
            if not isinstance(request, dict) or type(request.get("id")) is not int:
                raise ValueError("invalid request envelope")
            request_id = request["id"]
            if request.get("command") == "shutdown":
                del model
                gc.collect()
                _send_response({"event": "shutdown", "id": request_id})
                return 0
            if not {"id", "audio_b64"} <= set(request) <= {"id", "audio_b64", "hint", "hint_mode"}:
                raise ValueError("unsupported request fields")
            encoded_audio = request["audio_b64"]
            if not isinstance(encoded_audio, str):
                raise ValueError("audio payload must be base64 text")
            hint, hint_mode = _validated_hint(request)
            raw = base64.b64decode(encoded_audio, validate=True)
            response = _transcribe(model, request_id, raw, hint, hint_mode)
            _send_response(response)
            del raw, response
        except Exception as error:
            request_id = request.get("id") if isinstance(request, dict) else None
            _send_response(
                {
                    "id": request_id,
                    "ok": False,
                    "error_type": _error_type(error, device),
                    "message": f"Whisper inference failed ({_error_type(error, device)})",
                }
            )
    return 0


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--model", required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default=None)
    args = parser.parse_args()
    try:
        device, compute_type = validate_runtime(args.device, args.compute_type)
    except ValueError:
        _send_response(
            {
                "event": "startup_error",
                "error_type": "InvalidRuntime",
                "message": "Whisper device or compute type is not supported",
            }
        )
        return 1
    model_path = Path(args.model).resolve()
    if model_files(model_path) is None:
        _send_response(
            {
                "event": "startup_error",
                "error_type": "ModelUnavailable",
                "message": "Configured Whisper Small model is incomplete or missing",
            }
        )
        return 1
    if device == "cuda":
        try:
            enable_cuda_dlls()
        except CudaLibrariesMissing as error:
            _send_response(
                {
                    "event": "startup_error",
                    "error_type": "CudaLibrariesMissing",
                    "message": str(error)[:200],
                }
            )
            return 1
    return _worker_main(model_path, device, compute_type)


if __name__ == "__main__":
    raise SystemExit(main())


class PersistentWhisperWorker:
    def __init__(
        self,
        python_path,
        model_path,
        project_root,
        temporary_root,
        startup_timeout=None,
        request_timeout=REQUEST_TIMEOUT_SECONDS,
        device="cpu",
        compute_type=None,
    ):
        self.python_path = Path(python_path).resolve()
        self.model_path = Path(model_path).resolve()
        self.project_root = Path(project_root).resolve()
        self.temporary_root = Path(temporary_root).resolve()
        self.configuration = (str(self.python_path), str(self.model_path))
        # Voz 3: device and compute type; a change of either restarts the worker (see VoiceService).
        self.device, self.compute_type = validate_runtime(device, compute_type)
        self.runtime = (self.device, self.compute_type)
        if startup_timeout is None:
            startup_timeout = (
                GPU_STARTUP_TIMEOUT_SECONDS if self.device == "cuda" else MODEL_START_TIMEOUT_SECONDS
            )
        self.startup_timeout = startup_timeout
        self.request_timeout = request_timeout
        self.process = None
        self.job = None
        self.worker_pid = None
        self.worker_handle = None
        self.process_handles = {}
        self.responses = queue.Queue(maxsize=1)
        self.reader = None
        self.request_lock = threading.Lock()
        self.next_request_id = 1
        self.minimum_available_ram_bytes = None
        self.peak_working_set_bytes = 0
        self.peak_private_bytes = 0
        self.model_load_seconds = None
        self.startup_seconds = None
        self.last_used = None
        self.closed = False

    def command(self):
        return [
            str(self.python_path),
            "-B",
            "-m",
            "backend.app.voice.persistent_worker",
            "--model",
            str(self.model_path),
            "--device",
            self.device,
            "--compute-type",
            self.compute_type,
        ]

    def _attach_descendants(self):
        processes = process_snapshot()
        owned = _descendants(self.process.pid, processes)
        result = []
        for pid, parent_pid, executable in processes:
            if pid not in owned:
                continue
            if not _is_in_job(pid, self.job):
                _assign_to_job(pid, self.job)
            if not _is_in_job(pid, self.job):
                raise OSError(f"owned child process {pid} is outside the worker job")
            result.append((pid, parent_pid, executable))
        return result

    def _open_worker_handle(self, pid):
        if pid not in self.process_handles:
            self.process_handles[pid] = _open_process(
                pid, _PROCESS_QUERY_INFORMATION | _SYNCHRONIZE
            )
        self.worker_handle = self.process_handles[pid]
        self.worker_pid = pid

    def _adopt_worker_pid(self, pid):
        if type(pid) is not int or pid <= 0:
            raise WorkerProtocolError("Whisper startup PID is invalid")
        processes = process_snapshot()
        descendants = _descendants(self.process.pid, processes)
        if pid != self.process.pid and pid not in descendants:
            raise WorkerProtocolError(
                "Whisper startup PID is not part of the owned process tree"
            )
        if pid != self.process.pid and not _is_in_job(pid, self.job):
            _assign_to_job(pid, self.job)
            if not _is_in_job(pid, self.job):
                raise WorkerResourceMonitorError(
                    "the inference process could not be attached to its owned job"
                )
        self._open_worker_handle(pid)

    def _read_responses(self):
        while True:
            line = self.process.stdout.readline(MAX_RESPONSE_BYTES + 1)
            if not line:
                self.responses.put(("eof", None))
                return
            if len(line) > MAX_RESPONSE_BYTES or not line.endswith(b"\n"):
                self.responses.put(("invalid", None))
                return
            self.responses.put(("line", line))

    def _monitor(self):
        try:
            available = available_system_memory()
        except OSError as error:
            raise WorkerResourceMonitorError(
                "available system RAM could not be measured"
            ) from error
        self.minimum_available_ram_bytes = min(
            self.minimum_available_ram_bytes, available
        )
        if available < ABORT_RAM_BYTES:
            raise WorkerMemoryLimitError(
                "Whisper stopped because available system RAM fell below 1.5 GiB"
            )
        try:
            owned = self._attach_descendants()
        except OSError as error:
            raise WorkerResourceMonitorError(
                "Whisper process ownership could not be verified"
            ) from error
        current_pids = {self.process.pid, *(pid for pid, _, _ in owned)}
        if self.worker_pid and self.worker_pid not in current_pids:
            raise WorkerCrashedError("Whisper inference process exited unexpectedly")
        for pid in set(self.process_handles) - current_pids:
            _kernel.CloseHandle(self.process_handles.pop(pid))
            if pid == self.worker_pid:
                self.worker_handle = None
        for pid in current_pids:
            if pid not in self.process_handles:
                try:
                    self.process_handles[pid] = _open_process(
                        pid, _PROCESS_QUERY_INFORMATION | _SYNCHRONIZE
                    )
                except OSError as error:
                    if pid not in {entry[0] for entry in process_snapshot()}:
                        continue
                    raise WorkerResourceMonitorError(
                        f"owned process {pid} memory handle could not be opened"
                    ) from error
            try:
                working, private = process_memory(self.process_handles[pid])
            except OSError as error:
                if pid not in {entry[0] for entry in process_snapshot()}:
                    _kernel.CloseHandle(self.process_handles.pop(pid))
                    if pid == self.worker_pid:
                        self.worker_handle = None
                    continue
                raise WorkerResourceMonitorError(
                    f"owned process {pid} memory could not be measured"
                ) from error
            self.peak_working_set_bytes = max(self.peak_working_set_bytes, working)
            self.peak_private_bytes = max(self.peak_private_bytes, private)
        return available

    def _read_response(self, timeout, phase):
        deadline = time.monotonic() + timeout
        while True:
            self._monitor()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WorkerTimeoutError(f"Whisper {phase} exceeded its timeout")
            try:
                kind, line = self.responses.get(timeout=min(POLL_SECONDS, remaining))
            except queue.Empty:
                continue
            if kind == "eof":
                raise WorkerCrashedError("Whisper worker closed its response pipe")
            if kind != "line":
                raise WorkerProtocolError("Whisper worker response exceeded its limit")
            try:
                response = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise WorkerProtocolError("Whisper worker returned invalid JSON") from error
            if not isinstance(response, dict):
                raise WorkerProtocolError("Whisper worker returned an invalid response")
            return response

    def start(self):
        if os.name != "nt":
            raise WorkerStartupError("persistent Whisper worker requires Windows")
        if self.process:
            raise RuntimeError("Whisper worker has already started")
        if not self.python_path.is_file():
            raise WorkerStartupError("configured Whisper Python executable is missing")
        if model_files(self.model_path) is None:
            raise WorkerStartupError("configured Whisper Small model is incomplete")
        try:
            initial_ram = available_system_memory()
        except OSError as error:
            raise WorkerResourceMonitorError(
                "available system RAM could not be measured"
            ) from error
        if initial_ram < MIN_LOAD_RAM_BYTES:
            raise WorkerMemoryLimitError(
                "Whisper Small requires at least 3 GiB of available system RAM"
            )
        self.minimum_available_ram_bytes = initial_ram
        self.temporary_root.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment.update(
            {
                "HF_HUB_OFFLINE": "1",
                "HF_HOME": str(self.temporary_root.parent / "hf-cache"),
                "HF_HUB_CACHE": str(self.temporary_root.parent / "hf-cache" / "hub"),
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8",
                "OMP_NUM_THREADS": "4",
                "OPENBLAS_NUM_THREADS": "2",
                "TEMP": str(self.temporary_root),
                "TMP": str(self.temporary_root),
            }
        )
        started = time.perf_counter()
        try:
            self.job = _create_kill_on_close_job()
            self.process = subprocess.Popen(
                self.command(),
                cwd=str(self.project_root),
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            _assign_to_job(self.process.pid, self.job)
            self.reader = threading.Thread(target=self._read_responses, daemon=True)
            self.reader.start()
            response = self._read_response(self.startup_timeout, "startup")
            if response.get("event") == "startup_error":
                raise startup_failure(response)
            if response.get("event") != "ready":
                raise WorkerProtocolError("Whisper worker omitted startup handshake")
            self._adopt_worker_pid(response.get("pid"))
            if self.peak_private_bytes < MIN_LOADED_PRIVATE_BYTES[self.device]:
                raise WorkerProtocolError(
                    "Whisper process memory reading is implausibly low after model load"
                )
            self.model_load_seconds = response["model_load_seconds"]
            self.startup_seconds = time.perf_counter() - started
            self.last_used = time.monotonic()
            return response
        except BaseException:
            self.terminate()
            raise

    def transcribe(self, audio, hint=None, hint_mode=None):
        """``hint`` (vocabulary) is optional; it never appears in logs or in the response."""
        validate_audio(audio)
        if not self.process or self.closed:
            raise WorkerCrashedError("Whisper worker is not running")
        with self.request_lock:
            request_id = self.next_request_id
            self.next_request_id += 1
            request = {
                "id": request_id,
                "audio_b64": base64.b64encode(audio).decode("ascii"),
            }
            if hint:
                request["hint"] = hint
                request["hint_mode"] = hint_mode
            encoded = json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n"
            if len(encoded) > MAX_REQUEST_BYTES:
                raise ValueError("Whisper request exceeds the protocol size limit")
            started = time.perf_counter()
            try:
                _write_all(self.process.stdin, encoded)
                response = self._read_response(self.request_timeout, "inference")
                if response.get("id") != request_id:
                    raise WorkerProtocolError("Whisper response ID did not match request")
                if not response.get("ok"):
                    # A GPU memory failure is not a WorkerInferenceError: the worker is terminated below.
                    raise inference_failure(response)
                if not isinstance(response.get("text"), str):
                    raise WorkerProtocolError("Whisper response did not contain text")
                response["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
                self.last_used = time.monotonic()
                return response
            except WorkerInferenceError:
                raise
            except BaseException:
                self.terminate()
                raise

    def abort(self):
        if self.job:
            _kernel.TerminateJobObject(self.job, 1)
        if self.process and self.process.poll() is None:
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

    def close(self):
        if self.closed or not self.process:
            return {"exited": True, "forced": False, "worker_pid": self.worker_pid}
        owned_pids = _descendants(self.process.pid, process_snapshot())
        owned_pids.add(self.process.pid)
        request_id = self.next_request_id
        self.next_request_id += 1
        try:
            _write_all(
                self.process.stdin,
                json.dumps(
                    {"id": request_id, "command": "shutdown"},
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n",
            )
            response = self._read_response(10, "graceful shutdown")
            if response.get("id") != request_id or response.get("event") != "shutdown":
                raise WorkerProtocolError("invalid Whisper shutdown acknowledgement")
            self.process.wait(timeout=10)
            self._close_handles()
            remaining = self._wait_for_exit(owned_pids, timeout=5)
            if remaining:
                raise WorkerCrashedError(
                    f"Whisper process tree remained after shutdown: {remaining}"
                )
            self.closed = True
            return {
                "exited": self.process.poll() is not None,
                "forced": False,
                "worker_pid": self.worker_pid,
                "owned_processes_remaining": [],
            }
        except BaseException:
            self.terminate()
            raise

    def _wait_for_exit(self, pids, timeout):
        deadline = time.monotonic() + timeout
        watched = set(pids)
        while True:
            active = watched.intersection(
                pid for pid, _, _ in process_snapshot()
            )
            if not active:
                return []
            if time.monotonic() >= deadline:
                return sorted(active)
            time.sleep(0.05)

    def _close_handles(self):
        for handle in self.process_handles.values():
            _kernel.CloseHandle(handle)
        self.process_handles.clear()
        self.worker_handle = None
        if self.job:
            _kernel.CloseHandle(self.job)
            self.job = None

    def terminate(self):
        if self.closed:
            return []
        owned_pids = set()
        if self.process:
            try:
                owned_pids = _descendants(self.process.pid, process_snapshot())
                owned_pids.add(self.process.pid)
            except OSError:
                owned_pids.add(self.process.pid)
        if self.job:
            _kernel.TerminateJobObject(self.job, 1)
        if self.process and self.process.poll() is None:
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        remaining = self._wait_for_exit(owned_pids, timeout=5) if owned_pids else []
        self._close_handles()
        self.closed = True
        if remaining:
            raise WorkerCrashedError(
                f"owned Whisper processes survived forced termination: {remaining}"
            )
        return []

    @property
    def alive(self):
        return bool(self.process and self.process.poll() is None and not self.closed)

    @property
    def memory_snapshot(self):
        if not self.worker_handle:
            return None
        working, private = process_memory(self.worker_handle)
        return {
            "working_set_bytes": working,
            "private_bytes": private,
            "peak_working_set_bytes": self.peak_working_set_bytes,
            "peak_private_bytes": self.peak_private_bytes,
            "minimum_available_ram_bytes": self.minimum_available_ram_bytes,
        }
