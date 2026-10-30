"""Conservative STT quality policy; scores are engine estimates, not probabilities."""
import array
import math
import re
import sys

ACRONYMS = {x.lower(): x for x in "VPN DNS DHCP BIOS SSD RAM CPU GPU IP USB NVMe TCP HTTP HTTPS WiFi".split()}

def normalize_transcript(text):
    return re.sub(r"\b(?:" + "|".join(ACRONYMS) + r")\b",
                  lambda m: ACRONYMS[m.group().lower()], text, flags=re.I).strip()

def audio_quality(pcm):
    samples = array.array("h", pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    count = len(samples)
    rms = math.sqrt(sum(x*x for x in samples) / max(1, count)) / 32768
    clipped = sum(abs(x) >= 32760 for x in samples) / max(1, count)
    # Very conservative silence floor, not a speech/noise classifier.
    return {"rms": round(rms, 6), "clipped_fraction": round(clipped, 6),
            "duration_seconds": count / 16000, "silence": rms < 0.0001}

def transcription_result(raw, words, quality):
    reasons = []
    if quality.get("silence") or not raw.strip():
        reasons.append("no_speech")
    if quality.get("clipped_fraction", 0) > 0.01:
        reasons.append("clipped_audio")
    # Segmenter caps at 28s: a voiced tail at that limit may be cut off.
    if quality.get("duration_seconds", 0) >= 27.9 and quality.get("tail_rms", 0) > 0.01:
        reasons.append("possibly_incomplete")
    scores = [w["conf"] for w in words if isinstance(w.get("conf"), (int, float))]
    if raw.strip() and (not scores or min(scores) < 0.65):
        reasons.append("low_confidence" if scores else "confidence_unavailable")
    return {"text": normalize_transcript(raw), "raw_text": raw,
            "normalized_text": normalize_transcript(raw), "words": words,
            "confidence_kind": "vosk_word_estimate_uncalibrated" if scores else None,
            "needs_clarification": bool(reasons), "quality_reasons": reasons,
            "clarification": "No pude reconocer la pregunta con suficiente claridad. Repítela completa o escríbela." if reasons else None,
            "audio_quality": quality, "engine": "vosk"}
