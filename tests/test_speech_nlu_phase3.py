"""Text/PCM fixtures, not evidence of human speech accuracy."""
import asyncio
import pytest
from backend.app.assistant.references import detect_reference
from backend.app.voice.transcription import normalize_transcript, audio_quality, transcription_result
from test_assistant_service import build, ask, StubKnowledge

REFERENCE_CASES = [
    ("Explícame el paso 4", "ordinal", 4),
    ("Explícame el cuarto paso", "ordinal", 4),
    ("Explícame el paso cuatro", "ordinal", 4),
    ("Explícame el paso 3 del procedimiento", "ordinal", 3),
    ("No, me refería al paso cinco", "ordinal", 5),
    ("¿Y el anterior?", "previous", None),
    ("¿Qué es un usuario final?", None, None),
    ("¿Cómo restauro la versión anterior de Windows?", None, None),
    ("¿Qué pasa si Windows sigue sin arrancar?", None, None),
    ("En el segundo equipo explícame el paso cuatro", "ordinal", 4),
]

@pytest.mark.parametrize("text,kind,number", REFERENCE_CASES)
def test_reference(text, kind, number):
    result = detect_reference(text)
    assert (result.get("kind") if result else None) == kind
    assert (result.get("number") if result else None) == number

@pytest.mark.parametrize("text", ["¿Qué es una VPN?", "¿Cómo funciona una VPN?", "¿Qué es una dirección IP?", "¿Cómo configuro el DNS?", "¿Cómo actualizo la BIOS?", "¿Qué es un SSD NVMe?", "¿Cómo reviso la memoria RAM?", "¿Qué hago si Windows no arranca?"])
def test_general_it(text):
    assert ask(build(), text)["route"] == "general"

def test_step_corrections_and_relative_sequence():
    svc=build(); sid="phase3"
    ask(svc, "pasos de COMPUTER PREPARATION", sid)
    for question, number in [("Explícame el paso cuatro",4),("¿Y el anterior?",3),("Explícame el paso 3",3),("No, me refería al paso cinco",5)]:
        assert f"Paso {number} de «COMPUTER PREPARATION»" in ask(svc,question,sid)["answer"]

def test_one_step_does_not_escape_procedure():
    svc=build()
    state={"active_steps":[{"text":"Only step"}], "document_steps":[{"text":"Other procedure"}]*5, "current_step_index":0}
    result=asyncio.run(svc._answer_movement(state,"ordinal",4))
    assert "No existe el paso 4" in result

def test_missing_context_clarifies_without_retrieval():
    svc=build()
    assert "¿De qué procedimiento" in ask(svc,"Explícame el paso 4")["answer"]
    assert not svc.knowledge.queries

def test_multiple_procedures_ask_for_choice():
    svc=build()
    ask(svc,"pasos de COMPUTER PREPARATION","multi")
    assert "Opciones:" in ask(svc,"todos los pasos","multi")["answer"]

def test_multiple_documents_ask_for_choice():
    svc=build(knowledge=StubKnowledge(sources=[{"document_id":"a","title":"A"},{"document_id":"b","title":"B"}]))
    assert "varios documentos" in ask(svc,"procedimiento interno")["answer"]

def test_normalization_does_not_reinterpret_homophones():
    text="vpn dns dhcp bios ssd ram cpu gpu IP NVMe Windows no arranca"
    assert normalize_transcript(text)=="VPN DNS DHCP BIOS SSD RAM CPU GPU IP NVMe Windows no arranca"
    assert normalize_transcript("una uve pequeña") == "una uve pequeña"

def test_silence_low_confidence_and_clipping():
    quality=audio_quality(bytes(32000))
    assert transcription_result("",[],quality)["needs_clarification"]
    result=transcription_result("vpn",[{"word":"vpn","conf":0.3}], {"silence":False})
    assert result["needs_clarification"] and result["raw_text"] == "vpn" and result["text"] == "VPN"
    assert transcription_result("vpn",[],{})["confidence_kind"] is None
    assert "clipped_audio" in transcription_result("vpn",[{"conf":1}],{"clipped_fraction":0.1})["quality_reasons"]

def test_confident_transcript_and_incomplete_clip():
    assert not transcription_result("dns",[{"conf":0.99}],{})["needs_clarification"]
    assert transcription_result("dns",[{"conf":0.99}],{"duration_seconds":28,"tail_rms":0.1})["needs_clarification"]


def test_general_followup_survives_social_turn():
    svc=build()
    ask(svc,"¿Qué es una VPN?","general-social")
    ask(svc,"gracias","general-social")
    result=ask(svc,"¿Y qué pasa si eso no funciona?","general-social")
    assert result["route"] == "general" and not svc.knowledge.queries

def test_lan_auth_still_blocks_voice_and_assistant(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    from backend.app.providers.mock import MockLLM
    monkeypatch.setenv("ITGLASSES_ALLOW_LAN_BINDING","true")
    monkeypatch.setenv("ITGLASSES_API_TOKENS","phase3-test-only")
    with TestClient(create_app(MockLLM())) as client:
        assert client.get("/api/v1/voice/status").status_code == 401
        assert client.post("/api/v1/assistant/chat",json={"message":"hola"}).status_code == 401
        assert client.get("/api/v1/voice/status",headers={"Authorization":"Bearer phase3-test-only"}).status_code == 200
