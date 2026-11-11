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
                result.update(
                    cpu_seconds=round(time.process_time() - started, 3),
                    peak_working_set_mib=peak_memory(),
                    engine=task.get("engine", "vosk"),
                )
                print(json.dumps(result))
                return
            engine = task.get("engine", "vosk")
            if engine == "whisper-small":
                model_path = Path(task["whisper_model"])
                if not model_path.is_absolute() or not (model_path / "model.bin").is_file():
                    raise ValueError("configured Whisper Small model is invalid")
                stage = "model_init"
                import av
                from faster_whisper import WhisperModel
                import faster_whisper.audio as faster_whisper_audio

                if int(av.__version__.split(".", 1)[0]) >= 19:
                    original_open = av.open

                    def compatible_open(*args, **kwargs):
                        kwargs.pop("metadata_errors", None)
                        return original_open(*args, **kwargs)

                    faster_whisper_audio.av.open = compatible_open

                model = WhisperModel(
                    str(model_path),
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=4,
                    num_workers=1,
                    local_files_only=True,
                )
                stage = "recognition"
                inference_started = time.perf_counter()
                segments, info = model.transcribe(
                    task["input"],
                    language="es",
                    task="transcribe",
                    beam_size=5,
                    best_of=5,
                    temperature=0.0,
                    condition_on_previous_text=False,
                    vad_filter=False,
                    word_timestamps=False,
                )
                segment_results = []
                parts = []
                for segment in segments:
                    parts.append(segment.text)
                    segment_results.append({
                        "start": segment.start,
                        "end": segment.end,
                        "text": segment.text,
                        "avg_logprob": segment.avg_logprob,
                        "no_speech_prob": segment.no_speech_prob,
                        "compression_ratio": segment.compression_ratio,
                    })
                raw_text = "".join(parts).strip()
                quality_reasons = ["no_speech"] if not raw_text else []
                result = {
                    "text": raw_text,
                    "raw_text": raw_text,
                    "normalized_text": raw_text,
                    "engine": "whisper-small",
                    "language": info.language,
                    "language_probability": info.language_probability,
                    "segments": segment_results,
                    "inference_seconds": round(time.perf_counter() - inference_started, 4),
                    "audio_quality": quality,
                    "needs_clarification": not bool(raw_text),
                    "quality_reasons": quality_reasons,
                    "clarification": (
                        "No pude reconocer la pregunta con suficiente claridad. Repítela completa o escríbela."
                        if not raw_text else None
                    ),
                }
            elif engine == "vosk":
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
                result["engine"] = "vosk"
            else:
                raise ValueError("unsupported speech recognition engine")
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
        result["cpu_seconds"] = round(time.process_time() - started, 3)
        if task.get("engine", "vosk") != "whisper-small":
            result["peak_working_set_mib"] = peak_memory()
        print(json.dumps(result))
    except Exception as exc:  # noqa: BLE001 - surface worker errors as JSON to the parent process
        error_result = {
            "error": "worker_error",
            "error_type": type(exc).__name__,
            "stage": stage,
            "engine": (
                task.get("engine", "vosk")
                if isinstance(task, dict) and task.get("kind") == "stt"
                else "piper"
            ),
        }
        try:
            print(json.dumps(error_result))
        except Exception:  # noqa: BLE001
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
