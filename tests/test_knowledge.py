import asyncio
import json
import re
import time
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.knowledge.models import Metadata, KnowledgeRequest, KnowledgeError
from backend.app.knowledge.service import KnowledgeService
from backend.app.knowledge.retrieval import exact_ids, intent
from backend.app.knowledge.store import normalize


class SyntheticEmbeddings:
    model = 'synthetic-test-only'
    calls = 0
    digest = 'synthetic-v1'
    async def fingerprint(self): return self.digest
    async def unload(self): pass
    async def embed(self, texts, query=False):
        self.calls += 1
        vectors = []
        for text in texts:
            text = normalize(text)
            vector = [0., 0., 0., 0.]
            for index, words in enumerate([
                ['windows', 'computadora', 'formate', 'usb', 'disco'],
                ['impresora', 'toner'], ['nomina', 'salario'], ['viajes', 'vacaciones']]):
                if any(word in text for word in words): vector[index] = 1.
            if not any(vector): vector[-1] = 1.
            array = np.array(vector, dtype=np.float32)
            vectors.append(array / np.linalg.norm(array))
        return np.array(vectors)


class Selector:
    calls = 0
    async def select(self, question, rows):
        self.calls += 1
        return [0]


async def add(svc, title, text, pid='24', key=None, version='1', authorized=True):
    return await svc.ingest('synthetic.md', text.encode(), Metadata(title=title, procedure_id=pid,
        procedure_key=key, version=version, authorized=authorized))


def service(tmp_path):
    return KnowledgeService(tmp_path, SyntheticEmbeddings(), Selector())


@pytest.mark.parametrize('text,expected', [('¿Cuál es el procedimiento 24?', ['24']), ('procedimiento 024', ['24']), ('procedure OPS-24', ['ops-24']), ('id KB123', ['kb123']), ('Windows 24', [])])
def test_identifiers(text, expected):
    assert exact_ids(text) == expected


@pytest.mark.parametrize('text,expected', [('¿Cómo se llama el proceso? Dame su nombre.', 'identify'), ('¿Qué es formatear?', 'identify'), ('Necesito los pasos para reinstalar Windows', 'steps'), ('¿Cuál es el procedimiento 24?', 'identify')])
def test_intent(text, expected):
    assert intent(text) == expected


def test_acceptance_a_b_and_no_destructive_steps(tmp_path):
    async def scenario():
        svc = service(tmp_path)
        await add(svc, 'Formateo e instalación limpia de Windows', 'El procedimiento consiste en formatear el disco y reinstalar Windows.\n1. DESTRUCTIVE_SYNTHETIC_COMMAND')
        result = await svc.query(KnowledgeRequest(message='Tengo una computadora con Windows y necesito quitarle todo para instalarlo desde cero con una USB, pero no recuerdo cómo se llama ese proceso.'))
        assert result.status == 'answered' and result.sources
        assert 'DESTRUCTIVE' not in result.model_dump_json()
        source = svc.store.evidence(result.sources[0]['citation_id'])
        assert 'formatear' in source['text']
        calls = svc.selector.calls
        exact = await svc.query(KnowledgeRequest(message='¿Cuál es el procedimiento 24?'))
        assert exact.status == 'answered' and svc.selector.calls == calls
    asyncio.run(scenario())


def test_acceptance_c_d_and_pending_revocation(tmp_path):
    async def scenario():
        svc = service(tmp_path)
        computer = await add(svc, 'Formateo de Windows', 'Formatear el disco y reinstalar Windows en computadoras.')
        await add(svc, 'Tóner de impresoras', 'Sustitución de tóner de impresora.')
        await add(svc, 'Revisión de nómina', 'Control de nómina y salario.')
        calls = svc.embeddings.calls
        ambiguous = await svc.query(KnowledgeRequest(message='¿Cuál es el procedimiento 24?'))
        assert ambiguous.status == 'clarification_required' and len(ambiguous.options) == 3
        assert svc.embeddings.calls == calls and svc.selector.calls == 0
        result = await svc.query(KnowledgeRequest(message='El de las computadoras, donde se borra Windows.', conversation_id=ambiguous.conversation_id))
        assert result.status == 'answered' and result.sources[0]['document_id'] == computer['id']
        citation = result.sources[0]['citation_id']
        await svc.access(computer['id'], authorized=False)
        assert svc.store.evidence(citation) is None
        missing = await svc.query(KnowledgeRequest(message='El de Windows', conversation_id=ambiguous.conversation_id))
        assert missing.status == 'insufficient_evidence' and not missing.sources
    asyncio.run(scenario())


def test_e_missing(tmp_path):
    async def scenario():
        svc = service(tmp_path)
        await add(svc, 'Windows', 'Formatear el disco y reinstalar Windows.')
        result = await svc.query(KnowledgeRequest(message='¿Cómo se autorizan las vacaciones de la empresa?'))
        assert result.status == 'insufficient_evidence' and not result.sources
    asyncio.run(scenario())


def test_f_conflicting_versions(tmp_path):
    async def scenario():
        svc = service(tmp_path)
        first = await add(svc, 'Windows v1', 'Windows requiere el método A.', key='windows-reinstall', version='1')
        await add(svc, 'Windows v2', 'Windows requiere el método B.', key='windows-reinstall', version='2')
        result = await svc.query(KnowledgeRequest(message='Procedimiento 24'))
        assert result.status == 'conflict' and svc.selector.calls == 0
        chosen = await svc.query(KnowledgeRequest(message='La versión 1', choice_document_id=first['id'], conversation_id=result.conversation_id))
        assert chosen.status == 'answered'
    asyncio.run(scenario())


def test_steps_require_explicit_request_and_documented_steps(tmp_path):
    async def scenario():
        svc = service(tmp_path)
        await add(svc, 'Windows', '1. Paso sintético de prueba para Windows.\n2. Segundo paso sintético.')
        response = await svc.query(KnowledgeRequest(message='Dame los pasos para Windows'))
        assert response.status == 'answered' and 'Paso sintético' in response.answer
        assert response.sources[0]['quote'] in response.answer
    asyncio.run(scenario())


def test_no_steps_in_definition_document(tmp_path):
    async def scenario():
        svc = service(tmp_path)
        await add(svc, 'Windows', 'Reinstalar Windows significa reemplazar la instalación.')
        response = await svc.query(KnowledgeRequest(message='Dame los pasos para Windows'))
        assert response.status == 'insufficient_evidence'
    asyncio.run(scenario())


def test_withdrawal_during_generation_invalidates_source(tmp_path):
    async def scenario():
        svc = service(tmp_path)
        doc = await add(svc, 'Windows', 'Formatear Windows.')
        class RevokingSelector:
            async def select(self, question, rows):
                svc.store.access(doc['id'], withdrawn=True)
                return [0]
        svc.selector = RevokingSelector()
        result = await svc.query(KnowledgeRequest(message='¿Cómo se llama lo de Windows?'))
        assert result.status == 'insufficient_evidence' and not result.sources
    asyncio.run(scenario())


def test_unauthorized_default_and_unknown_conversation(tmp_path):
    async def scenario():
        svc = service(tmp_path)
        await add(svc, 'Windows', 'Windows text', authorized=False)
        assert not svc.store.eligible_chunks()
        assert (await svc.query(KnowledgeRequest(message='procedimiento 24'))).status == 'insufficient_evidence'
        with pytest.raises(KnowledgeError):
            await svc.query(KnowledgeRequest(message='test', conversation_id='00000000-0000-0000-0000-000000000000'))
    asyncio.run(scenario())


def test_long_question_and_model_fingerprint(tmp_path):
    async def scenario():
        svc = service(tmp_path)
        await add(svc, 'Windows', 'Formatear Windows.')
        result = await svc.query(KnowledgeRequest(message='Una explicación extensa. ' * 100 + 'Windows'))
        assert result.status == 'clarification_required' and result.options
        svc.embeddings.digest = 'changed'
        with pytest.raises(KnowledgeError, match='embedding model changed'):
            await svc.query(KnowledgeRequest(message='Windows'))
    asyncio.run(scenario())


def test_endpoint_lifecycle_and_citation_access(tmp_path):
    svc = service(tmp_path)
    with TestClient(create_app(knowledge=svc)) as client:
        response = client.post('/api/v1/documents', files={'file': ('test.txt', b'Synthetic Windows procedure.')}, data={'metadata': json.dumps({'title':'Windows', 'procedure_id':'24', 'authorized': True})})
        assert response.status_code == 201, response.text
        identifier = response.json()['id']
        query = client.post('/api/v1/knowledge/chat', json={'message':'procedimiento 24'})
        assert query.status_code == 200, query.text
        citation = query.json()['sources'][0]
        assert client.get(citation['url']).json()['quote'] == 'Synthetic Windows procedure.'
        assert client.post(f'/api/v1/documents/{identifier}/reindex').status_code == 200
        assert client.get(citation['url']).status_code == 404
        assert client.delete(f'/api/v1/documents/{identifier}').status_code == 200
        assert client.post('/api/v1/knowledge/chat', json={'message':'procedimiento 24'}).json()['status'] == 'insufficient_evidence'
        assert client.post('/api/v1/knowledge/chat', json={'message':'x' * 16001}).status_code == 422
        assert client.post('/api/v1/documents', headers={'origin':'https://untrusted.example'}, files={'file':('x.txt',b'x')}, data={'metadata':'{}'}).status_code == 403
