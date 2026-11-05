"""Persistent API authentication contract; no real credentials or external services."""
import pytest
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.providers.mock import MockLLM

@pytest.fixture
def client(monkeypatch):
    for name in ('ITGLASSES_REQUIRE_AUTH','ITGLASSES_ALLOW_LAN_BINDING','ITGLASSES_API_TOKENS'):
        monkeypatch.delenv(name,raising=False)
    with TestClient(create_app(MockLLM())) as client:
        yield client

@pytest.mark.parametrize('flag',['ITGLASSES_REQUIRE_AUTH','ITGLASSES_ALLOW_LAN_BINDING'])
@pytest.mark.parametrize('path,payload',[
    ('/api/v1/chat',{'message':'hola'}),
    ('/api/v1/assistant/chat',{'message':'hola'}),
    ('/api/v1/knowledge/chat',{'message':'hola'}),
    ('/api/v1/voice/transcribe',None),
    ('/api/v1/voice/synthesize',{'text':'hola'}),
])
def test_sensitive_routes_require_bearer(client,monkeypatch,flag,path,payload):
    monkeypatch.setenv(flag,'true')
    monkeypatch.setenv('ITGLASSES_API_TOKENS','test-token-only')
    result=client.post(path,json=payload)
    assert result.status_code==401
    assert 'test-token-only' not in result.text

@pytest.mark.parametrize('header',[None,'Basic abc','Bearer ','Bearer wrong','Bearer TEST-TOKEN-ONLY'])
def test_invalid_credentials_fail_closed(client,monkeypatch,header):
    monkeypatch.setenv('ITGLASSES_REQUIRE_AUTH','1')
    monkeypatch.setenv('ITGLASSES_API_TOKENS','test-token-only')
    headers={} if header is None else {'Authorization':header}
    assert client.post('/api/v1/chat',json={'message':'hola'},headers=headers).status_code==401

@pytest.mark.parametrize('token',['first-test-only','second-test-only'])
def test_configured_bearers_work(client,monkeypatch,token):
    monkeypatch.setenv('ITGLASSES_REQUIRE_AUTH','yes')
    monkeypatch.setenv('ITGLASSES_API_TOKENS',' first-test-only, , second-test-only ')
    response=client.post('/api/v1/chat',json={'message':'hola'},headers={'Authorization':'bEaReR '+token})
    assert response.status_code==200
    assert 'set-cookie' not in response.headers

def test_empty_token_configuration_denies_all(client,monkeypatch):
    monkeypatch.setenv('ITGLASSES_REQUIRE_AUTH','true')
    assert client.post('/api/v1/chat',json={'message':'hola'},headers={'Authorization':'Bearer anything'}).status_code==401

def test_cookies_and_query_tokens_cannot_bypass_bearer(client,monkeypatch):
    monkeypatch.setenv('ITGLASSES_REQUIRE_AUTH','1')
    monkeypatch.setenv('ITGLASSES_API_TOKENS','test-token-only')
    client.cookies.set('session','test-token-only')
    client.cookies.set('itglasses_session','test-token-only')
    assert client.post('/api/v1/chat?token=test-token-only',json={'message':'hola'}).status_code==401

def test_local_development_and_public_health_preserved(client,monkeypatch):
    assert client.post('/api/v1/chat',json={'message':'hola'}).status_code==200
    monkeypatch.setenv('ITGLASSES_REQUIRE_AUTH','1')
    assert client.get('/health').status_code==200
    assert client.get('/api/v1/status').status_code==200

def test_document_and_voice_status_protected(client,monkeypatch):
    monkeypatch.setenv('ITGLASSES_ALLOW_LAN_BINDING','1')
    assert client.get('/api/v1/documents').status_code==401
    assert client.get('/api/v1/voice/status').status_code==401

def test_lan_config_file_also_requires_auth(client,monkeypatch):
    from dataclasses import replace
    from backend.app.api import auth
    monkeypatch.setattr(auth,'settings',replace(auth.settings,host='0.0.0.0',allow_lan_binding=True))
    assert client.post('/api/v1/chat',json={'message':'hola'}).status_code==401
