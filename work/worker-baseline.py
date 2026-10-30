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
    task = json.load(sys.stdin)
    models = Path(task["models"])
    if task["kind"] == "stt":
        from vosk import Model, KaldiRecognizer, SetLogLevel
        SetLogLevel(-1)
        model = Model(str(models / "vosk-model-small-es-0.42"))
        recognizer = KaldiRecognizer(model, 16000)
        parts = []
        with wave.open(task["input"], "rb") as audio:
            while data := audio.readframes(4000):
                if recognizer.AcceptWaveform(data):
                    parts.append(json.loads(recognizer.Result()).get("text", ""))
        parts.append(json.loads(recognizer.FinalResult()).get("text", ""))
        result = {"text": " ".join(part for part in parts if part), "engine": "vosk"}
    else:
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
        with wave.open(task["output"], "wb") as audio:
            voice.synthesize_wav(task["text"], audio, syn_config=configuration)
        result = {"engine": "piper", "execution_providers": session.get_providers(), "threads": 2}
    result.update(cpu_seconds=round(time.process_time() - started, 3), peak_working_set_mib=peak_memory())
    print(json.dumps(result))


if __name__ == "__main__":
    main()
