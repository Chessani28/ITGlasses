"""One CPU inference per process; unload speech models before chat inference."""
import ctypes
import json
from pathlib import Path
import sys
import time
import wave


def peak_memory():
    if sys.platform != "win32":
        return None
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong)] + [(name, ctypes.c_size_t) for name in
            ["PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
             "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage"]]
    counters = Counters(); counters.cb = ctypes.sizeof(counters)
    ctypes.windll.kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    return round(counters.PeakWorkingSetSize / 2**20, 2)


def main():
    started = time.process_time()
    task = {}
    stage = "task"
    try:
        task = json.load(sys.stdin)
        models = Path(task["models"])
        if task["kind"] == "stt":
            from .transcription import audio_quality, transcription_result
            stage = "audio_read"
            with wave.open(task["input"], "rb") as audio:
                pcm = audio.readframes(audio.getnframes())
            quality = audio_quality(pcm)
            # Use min(6400, len(pcm)) to avoid slicing issues with short audio
            pcm_tail = pcm[-6400:] if len(pcm) >= 6400 else pcm
            quality["tail_rms"] = audio_quality(pcm_tail)["rms"]
            if quality["silence"]:
                result = transcription_result("", [], quality)
                result.update(cpu_seconds=round(time.process_time() - started, 3), peak_working_set_mib=peak_memory())
                print(json.dumps(result))
                return
            stage = "model_init"
            from vosk import Model, KaldiRecognizer, SetLogLevel
            SetLogLevel(-1)
            model = Model(str(models / "vosk-model-small-es-0.42"))
            recognizer = KaldiRecognizer(model, 16000)
            recognizer.SetWords(True)
            stage = "recognition"
            parts = []
            words = []
            with wave.open(task["input"], "rb") as audio:
                while data := audio.readframes(4000):
                    if recognizer.AcceptWaveform(data):
                        segment = json.loads(recognizer.Result())
                        parts.append(segment.get("text", ""))
                        words.extend(segment.get("result", []))
            segment = json.loads(recognizer.FinalResult())
            parts.append(segment.get("text", ""))
            words.extend(segment.get("result", []))
            result = transcription_result(" ".join(part for part in parts if part), words, quality)
        else:
            stage = "tts_model_init"
            from piper import PiperVoice, SynthesisConfig
            import onnxruntime
            from piper.config import PiperConfig
            options = onnxruntime.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            options.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL
            model_path = models / "es_ES-sharvard-medium.onnx"
            config = PiperConfig.from_dict(json.loads(model_path.with_suffix(".onnx.json").read_text(encoding="utf-8")))
            session = onnxruntime.InferenceSession(str(model_path), sess_options=options, providers=["CPUExecutionProvider"])
            voice = PiperVoice(session=session, config=config, download_dir=models)
            configuration = SynthesisConfig(speaker_id=task["speaker"], length_scale=1 / task["speed"])
            stage = "synthesis"
            with wave.open(task["output"], "wb") as audio:
                voice.synthesize_wav(task["text"], audio, syn_config=configuration)
            result = {"engine": "piper", "execution_providers": session.get_providers(), "threads": 2}
        result.update(cpu_seconds=round(time.process_time() - started, 3), peak_working_set_mib=peak_memory())
        print(json.dumps(result))
    except Exception as exc:  # noqa: BLE001 - surface worker errors as JSON to the parent process
        error_result = {
            "error": "worker_error",
            "error_type": type(exc).__name__,
            "stage": stage,
            "engine": "vosk" if isinstance(task, dict) and task.get("kind") == "stt" else "piper",
        }
        try:
            print(json.dumps(error_result))
        except Exception:  # noqa: BLE001
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
