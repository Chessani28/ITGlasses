"""Voz 2: the persistent Whisper worker accepts an optional vocabulary hint and drops hint echoes."""
import array
import io
import types
import wave

import pytest

from backend.app.voice import persistent_worker as pw


def speech_wav(seconds=0.5, level=6000):
    samples = array.array("h", [level if i % 2 else -level for i in range(int(16000 * seconds))])
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(samples.tobytes())
    return output.getvalue()


def silent_wav(seconds=0.5):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * int(16000 * seconds))
    return output.getvalue()


class FakeModel:
    def __init__(self, text="hola", no_speech=0.1):
        self.calls = []
        self.text, self.no_speech = text, no_speech

    def transcribe(self, samples, **options):
        self.calls.append(options)
        segment = types.SimpleNamespace(start=0.0, end=0.5, text=self.text, avg_logprob=-0.2,
                                        no_speech_prob=self.no_speech, compression_ratio=1.1)
        return iter([segment]), types.SimpleNamespace(language_probability=0.99)


@pytest.mark.parametrize("request_fields", [{}, {"hint": None, "hint_mode": None}])
def test_requests_without_hint_stay_valid(request_fields):
    assert pw._validated_hint(request_fields) == (None, None)


@pytest.mark.parametrize("request_fields", [
    {"hint": "Training Setup Guideline", "hint_mode": "prompt"},
    {"hint": "Training Setup Guideline"},
    {"hint_mode": "hotwords"},
    {"hint": "   ", "hint_mode": "hotwords"},
    {"hint": 42, "hint_mode": "hotwords"},
    {"hint": "x" * (pw.MAX_HINT_CHARS + 1), "hint_mode": "hotwords"},
])
def test_invalid_hints_are_protocol_errors(request_fields):
    with pytest.raises(ValueError):
        pw._validated_hint(request_fields)


@pytest.mark.parametrize("mode", pw.HINT_MODES)
def test_hint_reaches_faster_whisper_in_the_chosen_mode(mode):
    model = FakeModel()
    response = pw._transcribe(model, 1, speech_wav(), "Training Setup Guideline, BIOS", mode)
    assert model.calls[0][mode] == "Training Setup Guideline, BIOS"
    other = {"hotwords", "initial_prompt"} - {mode}
    assert not other & set(model.calls[0])
    assert response["hint_used"] is True and response["hint_mode"] == mode
    assert "Training Setup Guideline" not in str({k: v for k, v in response.items() if k != "text"})


def test_without_hint_the_decoding_is_unchanged():
    model = FakeModel()
    response = pw._transcribe(model, 1, speech_wav())
    assert "hotwords" not in model.calls[0] and "initial_prompt" not in model.calls[0]
    assert response["hint_used"] is False and response["hint_mode"] is None


def test_silence_is_not_decoded_even_with_a_hint():
    model = FakeModel()
    response = pw._transcribe(model, 1, silent_wav(), "Training Setup Guideline", "hotwords")
    assert model.calls == [] and response["text"] == "" and response["hint_used"] is False


def test_a_hint_echo_over_no_speech_becomes_no_speech():
    model = FakeModel(text=" Training Setup Guideline, BIOS.", no_speech=0.8)
    response = pw._transcribe(model, 1, speech_wav(), "Training Setup Guideline, BIOS, SSD", "hotwords")
    assert response["text"] == "" and response["needs_clarification"] is True


@pytest.mark.parametrize(("text", "no_speech"), [
    (" Training Setup Guideline, BIOS.", 0.2),                  # real speech: Whisper is confident
    (" Según el manual de Training Setup Guideline", 0.8),       # words beyond the hint
])
def test_real_speech_is_never_taken_for_an_echo(text, no_speech):
    model = FakeModel(text=text, no_speech=no_speech)
    response = pw._transcribe(model, 1, speech_wav(), "Training Setup Guideline, BIOS, SSD", "hotwords")
    assert response["text"] == text.strip()
