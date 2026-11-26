"""Whisper with the vocabulary hint can hallucinate (measured with real recordings: Small on the CPU repeated
"Procedimiento de emergencia para VPN" ~25 times for "Para qué sirve DHCP"). Such a transcript is detected and the
audio transcribed again without the hint."""
import pytest

from backend.app.voice.hallucination import looks_hallucinated
from test_voice_stt_hints import HintWorker, Knowledge, Request, Store, transcribe, whisper  # noqa: F401

LOOP = ", ".join(["Procedimiento de emergencia para VPN"] * 6)


@pytest.mark.parametrize("text,seconds", [
    (LOOP, 2.0),
    ("hola " * 60, 3.0),                                   # far more text than fits in the audio
    ("instala el cliente instala el cliente instala el cliente instala el cliente", 4.0),
])
def test_loops_and_impossible_lengths_are_hallucinations(text, seconds):
    assert looks_hallucinated(text, seconds)


@pytest.mark.parametrize("text,seconds", [
    ("¿Cuál es la versión más reciente de Windows 11?", 3.0),
    ("Dame los pasos, los pasos de la VPN por favor", 4.0),  # one repeat is normal speech
    ("Oye, ¿puedes ayudarme con mi computadora?", 2.5),
    ("", 1.0),
])
def test_normal_transcripts_are_not(text, seconds):
    assert not looks_hallucinated(text, seconds)


class LoopingWithHint(HintWorker):
    def transcribe(self, audio, hint=None, hint_mode=None):
        self.text = LOOP if hint else "Para qué sirve DHCP"
        return super().transcribe(audio, hint, hint_mode)


def test_a_hallucinated_transcript_is_retried_without_the_hint(whisper, monkeypatch):  # noqa: F811
    from backend.app.voice import service as voice
    service, workers = whisper
    created = []

    def create(*args):
        worker = LoopingWithHint(*args[:2])
        created.append(worker)
        return worker
    monkeypatch.setattr(voice, "PersistentWhisperWorker", create)
    result = transcribe(service, Request(Knowledge(Store())))
    assert result["text"] == "Para qué sirve DHCP"
    assert [hint is not None for hint, _ in created[0].hints] == [True, False]
    assert result.get("hint_used") is False


def test_a_normal_transcript_is_not_retried(whisper):  # noqa: F811
    service, workers = whisper
    transcribe(service, Request(Knowledge(Store())))
    assert len(workers[0].hints) == 1
