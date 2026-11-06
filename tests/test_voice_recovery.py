"""Regression tests for the current Windows reload failure and recovery."""
import asyncio
import json
import subprocess
import sys
import pytest
from backend.app.voice.service import VoiceService
from backend.app.knowledge.models import KnowledgeError
import io
import wave


def wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(bytes(32000))
    return output.getvalue()
from test_assistant_service import build, ask

@pytest.mark.skipif(sys.platform != "win32", reason="Windows reload regression")
def test_selector_loop_transcribes_real_silence_worker(tmp_path, monkeypatch):
    svc=VoiceService(tmp_path)
    monkeypatch.setattr(svc,'status',lambda:{'stt_available':True})
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        result=runner.run(svc.run('stt',wav()))
    assert result['text']=='' and result['needs_clarification']
    assert not svc.busy and not list(svc.temporary.iterdir())

def test_worker_malformed_input_has_safe_structured_failure():
    result=subprocess.run([sys.executable,'-B','-m','backend.app.voice.worker'],input=b'not json',capture_output=True,timeout=10)
    assert result.stdout.startswith(b'{'), result.stderr
    data=json.loads(result.stdout)
    assert result.returncode != 0 and data['error']=='worker_error'
    assert data['error_type']=='JSONDecodeError' and data['stage']=='task'
    assert 'not json' not in result.stdout.decode()

class Process:
    def __init__(self,stdout=b'{}',rc=0): self.stdout=stdout; self.returncode=rc
    async def communicate(self,data):
        while self.returncode is None: await asyncio.sleep(0)
        return self.stdout,b''
    def kill(self): self.returncode=-1
    async def wait(self): return self.returncode

@pytest.mark.parametrize('stdout,rc',[(b'not json',0),(b'[]',0),(b'{}',0),(b'{"error":"worker_error","stage":"model_init","error_type":"RuntimeError","message":"PRIVATE_TRANSCRIPT"}',1)])
def test_worker_failures_return_502_and_release_service(tmp_path,monkeypatch,caplog,stdout,rc):
    svc=VoiceService(tmp_path); monkeypatch.setattr(svc,'status',lambda:{'stt_available':True})
    process=Process(stdout,rc)
    async def spawn(*a,**k): return process
    monkeypatch.setattr(asyncio,'create_subprocess_exec',spawn)
    with pytest.raises(KnowledgeError) as error: asyncio.run(svc.run('stt',wav()))
    assert error.value.status==502 and 'PRIVATE_TRANSCRIPT' not in str(error.value)
    assert 'PRIVATE_TRANSCRIPT' not in caplog.text
    if rc: assert 'model_init' in caplog.text and 'RuntimeError' in caplog.text
    assert not svc.busy and not list(svc.temporary.iterdir())

def test_timeout_kills_worker_and_releases_lock(tmp_path,monkeypatch):
    svc=VoiceService(tmp_path); monkeypatch.setattr(svc,'status',lambda:{'stt_available':True})
    process=Process(rc=None)
    async def spawn(*a,**k): return process
    original_timeout=asyncio.timeout
    monkeypatch.setattr(asyncio,'create_subprocess_exec',spawn)
    monkeypatch.setattr(asyncio,'timeout',lambda seconds:original_timeout(.01))
    with pytest.raises(KnowledgeError) as error: asyncio.run(svc.run('stt',wav()))
    assert error.value.code=='voice_timeout' and process.returncode==-1
    assert not svc.busy and not list(svc.temporary.iterdir())

def test_printer_followup_keeps_subject_and_explicit_subject_switches():
    svc=build()
    assert ask(svc,'Mi impresora no funciona.','printer')['route']=='general'
    assert ask(svc,'No se conecta a mi computadora.','printer')['route']=='general'
    assert svc.sessions.get('printer')[1]['active_subject']=='impresora'
    assert 'impresora' in svc.provider.calls[-1] and 'No se conecta a mi computadora.' in svc.provider.calls[-1]
    ask(svc,'Mi computadora no arranca.','printer')
    assert svc.sessions.get('printer')[1]['active_subject']=='computadora'
    ask(svc,'No, me refería al router.','printer')
    assert svc.sessions.get('printer')[1]['active_subject']=='router'

def test_manual_request_overrides_general_context():
    svc=build(); ask(svc,'Mi impresora no funciona.','manual')
    result=ask(svc,'Según el manual, ¿qué mantenimiento necesitan las computadoras?','manual')
    assert result['route']=='knowledge'
    assert svc.knowledge.queries[-1].message.startswith('Según el manual')

def test_unrelated_session_does_not_inherit_printer():
    svc=build(); ask(svc,'Mi impresora no funciona.','printer')
    result=ask(svc,'No se conecta.','new')
    assert '¿' in result['answer'] and not svc.provider.calls[-1].endswith('No se conecta.')


@pytest.mark.parametrize('spoken,expected',[
    ('qué es una ve pe ene','qué es una VPN'),
    ('configuro el de ene ese','configuro el DNS'),
    ('dirección i pe','dirección IP'),
    ('memoria erre a eme','memoria RAM'),
    ('disco ese ese de ene uve eme e','disco SSD NVMe'),
    ('actualizar be i o ese','actualizar BIOS'),
    ('ce pe u y ge pe u','CPU y GPU'),
])
def test_explicit_spelled_acronyms_are_normalized_without_question_templates(spoken,expected):
    from backend.app.voice.transcription import transcription_result
    result=transcription_result(spoken,[{'conf':.99}],{})
    assert result['text']==expected and result['raw_text']==spoken

def test_ordinary_spanish_and_homophones_are_not_replaced():
    from backend.app.voice.transcription import transcription_result
    text='está bien, qué pena, labios y noviembre'
    assert transcription_result(text,[{'conf':.99}],{})['text']==text

def test_severe_uncertainty_clarifies_but_mild_warning_does_not():
    from backend.app.voice.transcription import transcription_result
    assert transcription_result('es una pena',[{'conf':.54}],{})['needs_clarification']
    assert not transcription_result('explícame el cuarto paso',[{'conf':.594}],{})['needs_clarification']
    assert transcription_result('pregunta',[],{})['needs_clarification']
    assert transcription_result('pregunta',[{'conf':1}],{'clipped_fraction':.03})['needs_clarification']


def test_explicit_conceptual_topic_change_clears_old_problem_subject():
    svc=build(); ask(svc,'Mi impresora no funciona.','change')
    ask(svc,'¿Qué es una VPN?','change')
    assert not svc.sessions.get('change')[1].get('active_subject')
    ask(svc,'¿Y cómo funciona?','change')
    assert 'Tema explícito de soporte activo: impresora' not in svc.provider.calls[-1]
