"""Deterministic speech transport tests; no downloaded models are required."""
import asyncio
import io
import json
import wave
from fastapi.testclient import TestClient
import pytest
from backend.app.main import create_app
from backend.app.providers.mock import MockLLM
from backend.app.voice.service import VoiceService, validate_audio
from backend.app.knowledge.models import KnowledgeError
from test_knowledge import service


def wav(rate=16000, channels=1, seconds=0.2):
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(channels); audio.setsampwidth(2); audio.setframerate(rate)
        audio.writeframes(b'\0\0' * int(rate * seconds) * channels)
    return output.getvalue()


class FakeVoice:
    def __init__(self): self.calls = []; self.after = None
    def status(self):
        return {'stt_available': True, 'stt_engine': 'vosk', 'tts_available': True, 'device': 'cpu',
            'stt_engines': {'vosk': {'available': True}, 'whisper-small': {'available': True}}}
    async def run(self, kind, payload, request=None, engine='vosk'):
        self.calls.append((kind, payload, engine))
        if self.after: await self.after()
        if kind == 'stt': return {'text': 'cuál es el procedimiento 24', 'elapsed_ms': 8, 'engine': engine}
        return {'audio': wav(), 'elapsed_ms': 8}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(MockLLM(), service(tmp_path), FakeVoice())) as client:
        yield client


def test_voice_status_and_stt_contract(client):
    status = client.get('/api/v1/voice/status').json()
    assert status['device'] == 'cpu' and status['stt_engine'] == 'vosk'
    response = client.post('/api/v1/voice/transcribe', content=wav(), headers={'Content-Type': 'audio/wav'})
    assert response.status_code == 200
    assert response.json()['text'] == 'cuál es el procedimiento 24'
    assert response.json()['engine'] == 'vosk'


def test_whisper_is_an_explicit_opt_in_and_reports_selected_engine(client):
    response = client.post('/api/v1/voice/transcribe', content=wav(), headers={
        'Content-Type': 'audio/wav', 'X-ITGlasses-STT-Engine': 'whisper-small'})
    assert response.status_code == 200
    assert response.json()['engine'] == 'whisper-small'
    assert client.app.state.voice.calls[-1][2] == 'whisper-small'


def test_unknown_stt_engine_is_rejected(client):
    response = client.post('/api/v1/voice/transcribe', content=wav(), headers={
        'Content-Type': 'audio/wav', 'X-ITGlasses-STT-Engine': 'remote'})
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'voice_engine_invalid'


@pytest.mark.parametrize('raw', [b'not audio', wav(rate=8000), wav(channels=2), wav()[:-10], wav(seconds=0.01), b'0' * 960045], ids=['invalid','rate','channels','truncated','short','oversized'])
def test_reject_invalid_audio(raw):
    with pytest.raises(KnowledgeError): validate_audio(raw)


def test_reject_compressed_browser_audio(client):
    assert client.post('/api/v1/voice/transcribe', content=b'webm', headers={'Content-Type':'audio/webm'}).status_code == 415


def test_local_origins_only(client):
    assert client.get('/api/v1/voice/status', headers={'Origin':'https://untrusted.example'}).status_code == 403


@pytest.mark.parametrize('payload', [{'text':''}, {'text':'x'*6001}, {'text':'hello', 'voice':'remote'},
    {'text':'hello', 'speaker':7}, {'text':'hello', 'speed':0}, {'text':'hello', 'command':'del files'}])
def test_tts_validation(client, payload):
    response = client.post('/api/v1/voice/synthesize', json=payload)
    assert response.status_code == 422 and response.json()['error']['code'] == 'voice_validation'


def test_tts_response_and_no_cache(client):
    response = client.post('/api/v1/voice/synthesize', json={'text':'Respuesta sintética.'})
    assert response.status_code == 200 and response.content.startswith(b'RIFF')
    assert response.headers['cache-control'] == 'no-store'


def test_stt_chat_knowledge_and_revoked_audio(client):
    transcript = client.post('/api/v1/voice/transcribe', content=wav(), headers={'Content-Type':'audio/wav'}).json()['text']
    assert client.post('/api/v1/chat', json={'message':transcript}).json()['provider'] == 'mock'
    doc = client.post('/api/v1/documents', files={'file':('windows.txt', b'Formatear Windows.')},
        data={'metadata':json.dumps({'title':'Windows', 'procedure_id':'24', 'authorized':True})}).json()
    answer = client.post('/api/v1/knowledge/chat', json={'message':transcript}).json()
    assert answer['status'] == 'answered' and answer['intent'] == 'identify'
    payload = {'text':answer['answer'], 'citation_ids':[source['citation_id'] for source in answer['sources']]}
    assert client.post('/api/v1/voice/synthesize', json=payload).status_code == 200
    client.patch(f"/api/v1/documents/{doc['id']}/authorization", json={'authorized':False})
    assert client.post('/api/v1/voice/synthesize', json=payload).status_code == 409


def test_withdrawal_during_synthesis_blocks_delivery(client):
    doc = client.post('/api/v1/documents', files={'file':('windows.txt', b'Formatear Windows.')},
        data={'metadata':json.dumps({'title':'Windows', 'procedure_id':'24', 'authorized':True})}).json()
    answer = client.post('/api/v1/knowledge/chat', json={'message':'procedimiento 24'}).json()
    async def withdraw(): await client.app.state.knowledge.access(doc['id'], withdrawn=True)
    client.app.state.voice.after = withdraw
    response = client.post('/api/v1/voice/synthesize', json={'text':answer['answer'], 'citation_ids':[s['citation_id'] for s in answer['sources']]})
    assert response.status_code == 409 and not response.content.startswith(b'RIFF')


def test_missing_models_are_explicit(tmp_path):
    svc = VoiceService(tmp_path)
    assert not svc.status()['stt_available'] and not svc.status()['tts_available']
    with pytest.raises(KnowledgeError) as error: asyncio.run(svc.run('stt', wav()))
    assert error.value.code == 'voice_unavailable'


def test_voice_assets_and_csp(client):
    for asset in ['voice-ui.mjs','voice-core.mjs','microphone.mjs','voice-worklet.js']:
        assert client.get('/ui/'+asset).status_code == 200
    assert "media-src 'self' blob:" in client.get('/').headers['content-security-policy']


def test_busy_worker_rejected(tmp_path, monkeypatch):
    svc = VoiceService(tmp_path); svc.busy = True
    with pytest.raises(KnowledgeError) as error: asyncio.run(svc.run('stt', wav()))
    assert error.value.code == 'voice_busy'


def test_disconnect_kills_only_owned_worker_and_cleans_request_files(tmp_path, monkeypatch):
    svc = VoiceService(tmp_path)
    monkeypatch.setattr(svc, '_vosk_available', lambda: True)
    class Process:
        returncode = None
        async def communicate(self, data):
            while self.returncode is None: await asyncio.sleep(0)
            return b'{}', None
        def kill(self): self.returncode = -1
        async def wait(self): return self.returncode
    process = Process()
    async def spawn(*args, **kwargs): return process
    class Request:
        async def is_disconnected(self): return True
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    with pytest.raises(KnowledgeError) as error: asyncio.run(svc.run('stt', wav(), Request()))
    assert error.value.code == 'voice_cancelled' and process.returncode == -1
    assert not svc.busy and not list(svc.temporary.iterdir())
