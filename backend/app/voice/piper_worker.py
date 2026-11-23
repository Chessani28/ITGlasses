"""Persistent Piper (TTS) worker: loads the voice once, then serves one synthesis per JSON line.

Protocol (UTF-8, one JSON object per line):
  started  -> stdout {"ready": true, "providers": [...], "load_seconds": n}   or {"error": "startup", ...}
  stdin    <- {"text": "...", "speaker": 0, "speed": 1.0, "output": "C:/.../output.wav"}
  stdout   -> {"ok": true, "cpu_seconds": n, "synthesis_seconds": n}           or {"error": "synthesis", ...}
The parent (piper_persistent.PersistentPiper) kills this process on any doubt; stdin EOF ends it.

CPU only: the same ONNX settings as backend.app.voice.worker (2 intra-op threads, sequential, CPUExecutionProvider),
so the audio is the same; the GPU is left to Whisper and Ollama. Never logs or returns the text.
"""
import json
import os
import sys
import time
from pathlib import Path

VOICE_NAME = "es_ES-sharvard-medium"


def load_voice(models):
    """The voice and its ONNX session, exactly as the per-request worker builds them."""
    from piper import PiperVoice
    from piper.config import PiperConfig
    import onnxruntime

    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL
    model_path = Path(models) / f"{VOICE_NAME}.onnx"
    config = PiperConfig.from_dict(json.loads(model_path.with_suffix(".onnx.json").read_text(encoding="utf-8")))
    session = onnxruntime.InferenceSession(str(model_path), sess_options=options, providers=["CPUExecutionProvider"])
    return PiperVoice(session=session, config=config, download_dir=Path(models)), session.get_providers()


def synthesize(voice, task):
    """One WAV at task["output"], with the same SynthesisConfig as the per-request worker."""
    import wave
    from piper import SynthesisConfig

    configuration = SynthesisConfig(speaker_id=task["speaker"], length_scale=1 / task["speed"])
    with wave.open(task["output"], "wb") as audio:
        voice.synthesize_wav(task["text"], audio, syn_config=configuration)


def _emit(stream, message):
    stream.write(json.dumps(message) + "\n")
    stream.flush()


def serve(models, stdin=sys.stdin, stdout=sys.stdout, load=load_voice, synth=synthesize):
    """Loop until stdin closes. [load] and [synth] are injectable for tests."""
    started = time.perf_counter()
    try:
        voice, providers = load(models)
    except Exception as exc:  # noqa: BLE001 - reported to the parent, which falls back to per-request synthesis
        _emit(stdout, {"error": "startup", "error_type": type(exc).__name__})
        return 1
    _emit(stdout, {"ready": True, "providers": list(providers), "load_seconds": round(time.perf_counter() - started, 3)})
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        cpu, wall = time.process_time(), time.perf_counter()
        try:
            task = json.loads(line)
            if not isinstance(task, dict) or not isinstance(task.get("text"), str) or not task.get("output"):
                raise ValueError("invalid task")
            synth(voice, task)
        except Exception as exc:  # noqa: BLE001 - one failed synthesis must not kill the loaded voice
            _emit(stdout, {"error": "synthesis", "error_type": type(exc).__name__})
            continue
        _emit(stdout, {"ok": True, "cpu_seconds": round(time.process_time() - cpu, 3),
                       "synthesis_seconds": round(time.perf_counter() - wall, 3)})
    return 0


def main():
    if len(sys.argv) != 2:
        _emit(sys.stdout, {"error": "startup", "error_type": "Usage"})
        return 2
    # Line-buffered UTF-8 pipes on every platform (Windows defaults to the ANSI code page).
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")
    return serve(sys.argv[1])


if __name__ == "__main__":
    sys.exit(main())
