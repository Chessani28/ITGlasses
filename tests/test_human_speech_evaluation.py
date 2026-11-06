import importlib.util
import json
from pathlib import Path
import wave
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/evaluate_human_speech.py'
spec = importlib.util.spec_from_file_location('human_eval', SCRIPT)
e = importlib.util.module_from_spec(spec)
if SCRIPT.exists():
    spec.loader.exec_module(e)


def test_alignment_counts_and_original_tokens():
    assert SCRIPT.exists(), 'human evaluator missing'
    result = e.align('uno dos tres', 'uno cuatro tres extra')
    assert (result['substitutions'], result['deletions'], result['insertions']) == (1, 0, 1)
    assert result['wer'] == pytest.approx(2 / 3)
    assert result['alignment'][1] == {'operation': 'substitute', 'reference': 'dos', 'hypothesis': 'cuatro'}
    assert e.align('uno dos', 'dos')['deletions'] == 1


def make_manifest(tmp_path):
    with wave.open(str(tmp_path / 'human.wav'), 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000); f.writeframes(b'\0\0' * 3200)
    data = [{'phrase_id': str(i), 'reference': p, 'path': 'human.wav', 'speaker_id': 'speaker-01',
             'locale': 'es-MX', 'provenance': 'human_recorded', 'condition': 'quiet', 'repeat': 1}
            for i, p in enumerate(e.PHRASES, 1)]
    path = tmp_path / 'manifest.json'; path.write_text(json.dumps(data), encoding='utf-8')
    return path, data


@pytest.mark.parametrize('provenance', ['synthetic', 'unknown', None])
def test_rejects_nonhuman(tmp_path, provenance):
    path, data = make_manifest(tmp_path); data[0]['provenance'] = provenance
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='human_recorded'): e.validate_manifest(path)


def test_missing_and_invalid_audio_and_coverage(tmp_path):
    path, data = make_manifest(tmp_path)
    data.pop(); path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='coverage'): e.validate_manifest(path)
    path, data = make_manifest(tmp_path); (tmp_path / 'human.wav').unlink()
    with pytest.raises(ValueError, match='WAV'): e.validate_manifest(path)
    (tmp_path / 'human.wav').write_bytes(b'invalid')
    with pytest.raises(ValueError, match='WAV'): e.validate_manifest(path)


def test_preserves_contract_and_http_failure(tmp_path, monkeypatch):
    path, data = make_manifest(tmp_path)
    payload = {'raw_text': 'que es una ve pe ene', 'normalized_text': 'qué es una VPN',
               'words': [{'word': 'ene', 'conf': .99}], 'needs_clarification': False,
               'quality_reasons': [], 'confidence_kind': 'estimate'}
    class Response:
        status_code = 200
        def json(self): return payload
    calls = []
    def post(url, **kwargs): calls.append((url, kwargs)); return Response()
    monkeypatch.setattr(e, 'post_audio', post)
    result = e.evaluate(path, 'http://127.0.0.1:8000', token='SECRET')
    row = result['cases'][0]
    assert row['reference'] == e.PHRASES[0]
    assert row['response'] == payload
    assert row['raw_wer']['wer'] > row['normalized_wer']['wer']
    assert row['elapsed_ms'] >= 0
    assert calls[0][0].endswith('/api/v1/voice/transcribe')
    assert calls[0][1]['headers']['Content-Type'] == 'audio/wav'
    Response.status_code = 401
    failed = e.evaluate(path, 'http://localhost:8000', token='SECRET')
    assert failed['cases'][0]['http_status'] == 401
    assert failed['cases'][0]['error'] == 'HTTP 401'
    assert 'SECRET' not in json.dumps(failed)


@pytest.mark.parametrize('url', ['https://example.com', 'http://127.0.0.1:8000/assistant', 'http://user:secret@localhost'])
def test_rejects_nonlocal_or_alternate_routes(url):
    with pytest.raises(ValueError): e.endpoint(url)

@pytest.mark.parametrize('field,value', [('raw_text', None), ('normalized_text', 3), ('words', ['oops']), ('needs_clarification', 'false'), ('quality_reasons', 'bad'), ('words', [{'word': 'x', 'conf': 1.1}]), ('words', [{'word': 'x', 'conf': True}])])
def test_malformed_response_is_reported(tmp_path, monkeypatch, field, value):
    path, _ = make_manifest(tmp_path)
    payload = {'raw_text': 'x', 'normalized_text': 'x', 'words': [], 'needs_clarification': False, 'quality_reasons': []}
    payload[field] = value
    class Response:
        status_code = 200
        def json(self): return payload
    monkeypatch.setattr(e, 'post_audio', lambda *a, **k: Response())
    assert e.evaluate(path, 'http://localhost')['cases'][0]['error'] == 'Invalid transcription response'


def test_oversized_wav_rejected_before_network(tmp_path, monkeypatch):
    path, _ = make_manifest(tmp_path)
    with (tmp_path / 'human.wav').open('ab') as f: f.write(b'X' * 960045)
    monkeypatch.setattr(e, 'post_audio', lambda *a, **k: pytest.fail('network must not occur'))
    with pytest.raises(ValueError, match='WAV'): e.evaluate(path, 'http://localhost')


@pytest.mark.parametrize('channels,width,rate,frames', [(2,2,16000,3200), (1,1,16000,3200), (1,2,8000,3200), (1,2,16000,10), (1,2,16000,480001)])
def test_rejects_wrong_wav_format(tmp_path, channels, width, rate, frames):
    path, _ = make_manifest(tmp_path)
    with wave.open(str(tmp_path / 'human.wav'), 'wb') as f:
        f.setnchannels(channels); f.setsampwidth(width); f.setframerate(rate)
        f.writeframes(b'\0' * frames * channels * width)
    with pytest.raises(ValueError, match='WAV'): e.validate_manifest(path)


def test_transport_failure_is_sanitized(tmp_path, monkeypatch):
    path, _ = make_manifest(tmp_path)
    def post(*args, **kwargs): raise e.httpx.ConnectError('SECRET')
    monkeypatch.setattr(e, 'post_audio', post)
    result = e.evaluate(path, 'http://localhost', token='SECRET')
    assert all(r['error'] == 'HTTP transport failure' for r in result['cases'])
    assert 'SECRET' not in json.dumps(result)


def test_http_client_disables_environment_and_redirects(monkeypatch):
    options = {}
    class Client:
        def __init__(self, **kwargs): options.update(kwargs)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kwargs): return (url, kwargs)
    monkeypatch.setattr(e.httpx, 'Client', Client)
    url, sent = e.post_audio('http://localhost/api/v1/voice/transcribe', data=b'wav', headers={'Content-Type':'audio/wav'})
    assert options['trust_env'] is False and options['follow_redirects'] is False
    assert options['timeout'].connect == 3
    assert sent['content'] == b'wav'

@pytest.mark.parametrize('field,value', [('speaker_id', None), ('path', None), ('condition', []), ('locale', None)])
def test_bad_manifest_metadata_fails_cleanly_before_network(tmp_path, monkeypatch, field, value):
    path, data = make_manifest(tmp_path)
    data[0][field] = value
    path.write_text(json.dumps(data))
    monkeypatch.setattr(e, 'post_audio', lambda *a, **k: pytest.fail('network must not occur'))
    with pytest.raises(ValueError): e.evaluate(path, 'http://localhost')


def test_nonobject_manifest_entry_fails_cleanly(tmp_path):
    path, data = make_manifest(tmp_path)
    data[0] = None
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError): e.validate_manifest(path)


def test_backend_error_code_is_preserved_without_message_secrets(tmp_path, monkeypatch):
    path, _ = make_manifest(tmp_path)
    class Response:
        status_code = 502
        def json(self): return {'error': {'code':'voice_inference_failed','message':'SECRET'}}
    monkeypatch.setattr(e,'post_audio',lambda *a,**k:Response())
    report=e.evaluate(path,'http://localhost',token='SECRET')
    assert report['cases'][0]['server_error_code']=='voice_inference_failed'
    assert 'SECRET' not in json.dumps(report)
