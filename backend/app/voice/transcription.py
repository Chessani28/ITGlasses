"""Conservative STT quality policy; scores are engine estimates, not probabilities.

Mild confidence warnings remain diagnostic. Severe uncertainty, absent scores,
clipping, incomplete recordings and silence request clarification.
"""
import array
import math
import re
import sys

ACRONYMS = {x.lower(): x for x in "VPN DNS DHCP BIOS SSD RAM CPU GPU IP USB NVMe TCP HTTP HTTPS WiFi".split()}

# Safe Vosk misrecognition corrections for common IT terms.
# Only applied when the recognized text strongly suggests the intended term,
# using exact single-token matches so ordinary phrases are never rewritten.
# Common Spanish words (e.g. "bien", "bene") are intentionally excluded to
# avoid false replacements; only unambiguous technical tokens are mapped.
VOSK_IT_TERMS = {
    "dns": "DNS",
    "dhcp": "DHCP",
    "bios": "BIOS",
    "ssd": "SSD",
    "ram": "RAM",
    "cpu": "CPU",
    "gpu": "GPU",
    "usb": "USB",
    "nvme": "NVMe",
    "tcp": "TCP",
    "http": "HTTP",
    "https": "HTTPS",
    "wifi": "WiFi",
    "router": "Router",
    "firewall": "Firewall",
    "servidor": "Servidor",
    "ethernet": "Ethernet",
    "outlook": "Outlook",
    "windows": "Windows",
    "vpn": "VPN",
    "ip": "IP",
    "ipaddress": "IP",
    "direccion": "Dirección",
    "direccion_ip": "Dirección IP",
    "dhcp": "DHCP",
}

# Vocabulary is word/letter-level, not a grammar of complete questions.
SPELLED_ACRONYMS = {
    "uve pe ene": "VPN", "ve pe ene": "VPN", "de ene ese": "DNS",
    "i pe": "IP", "erre a eme": "RAM", "ese ese de": "SSD",
    "ene uve eme e": "NVMe", "ene ve eme e": "NVMe", "be i o ese": "BIOS",
    "ce pe u": "CPU", "ge pe u": "GPU", "de hache ce pe": "DHCP",
}

def normalize_transcript(text):
    # Normalize acronym casing only; do not replace recognized words.
    return re.sub(r"\b(?:" + "|".join(ACRONYMS) + r")\b",
                  lambda m: ACRONYMS[m.group().lower()], text, flags=re.I).strip()

def _normalize_it_terms(text):
    """Normalize existing terms and explicit letter names; never guess homophones."""
    for spoken, acronym in sorted(SPELLED_ACRONYMS.items(), key=lambda pair: len(pair[0]), reverse=True):
        text = re.sub(r"\b" + re.escape(spoken).replace(r"\ ", r"\s+") + r"\b", acronym, text, flags=re.I)
    text = re.sub(r"\bwindows\s+(?:once|11)\b", "Windows 11", text, flags=re.I)
    text = re.sub(r"\bactive\s+directory\b", "Active Directory", text, flags=re.I)
    return re.sub(r"\b(?:" + "|".join(VOSK_IT_TERMS) + r")\b",
                  lambda match: VOSK_IT_TERMS[match.group().lower()], text, flags=re.I)


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
    scores = [w["conf"] for w in words if type(w.get("conf")) in (int, float)
              and math.isfinite(w["conf"]) and 0 <= w["conf"] <= 1]
    if len(scores) != len(words):
        reasons.append("confidence_unavailable")
    if raw.strip() and (not scores or min(scores) < 0.65):
        reasons.append("low_confidence" if scores else "confidence_unavailable")
    if scores and min(scores) < 0.55:
        reasons.append("very_low_confidence")
    blocked = bool(set(reasons) & {"no_speech", "very_low_confidence", "confidence_unavailable", "clipped_audio", "possibly_incomplete"})
    normalized = normalize_transcript(raw)
    normalized = _normalize_it_terms(normalized)
    return {"text": normalized, "raw_text": raw,
            "normalized_text": normalized, "words": words,
            "confidence_kind": "vosk_word_estimate_uncalibrated" if scores else None,
            "needs_clarification": blocked, "quality_reasons": reasons,
            "clarification": "No pude reconocer la pregunta con suficiente claridad. Repítela completa o escríbela." if blocked else None,
            "audio_quality": quality, "engine": "vosk"}
