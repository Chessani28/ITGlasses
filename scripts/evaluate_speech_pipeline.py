"""Local validation with real models and isolated synthetic conversations."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]; sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from test_speech_nlu_phase3 import REFERENCE_CASES
from backend.app.assistant.references import detect_reference
spec=importlib.util.spec_from_file_location('baseline',ROOT/'work/references-baseline.py')
baseline=importlib.util.module_from_spec(spec); spec.loader.exec_module(baseline)
def score(fn):
    rows=[]
    for text,kind,number in REFERENCE_CASES:
        result=fn(text)
        passed=(result.get('kind') if result else None)==kind and (result.get('number') if result else None)==number
        rows.append({'text':text,'expected_kind':kind,'expected_number':number,'actual':result,'passed':passed})
    return {'correct':sum(r['passed'] for r in rows),'total':len(rows),'cases':rows}
report={'reference_baseline':score(baseline.detect_reference),'reference_improved':score(detect_reference)}
from fastapi.testclient import TestClient
from backend.app.main import create_app
with TestClient(create_app()) as client:
    start=time.perf_counter()
    report['status']=client.get('/api/v1/status').json()
    # No private documents or recordings used in this probe.
    audio=(ROOT/'work/speech-eval/synthetic-07.wav').read_bytes()
    stt=client.post('/api/v1/voice/transcribe',content=audio,headers={'Content-Type':'audio/wav'})
    report['stt_status']=stt.status_code; report['stt']=stt.json()
    if stt.status_code==200 and not stt.json().get('needs_clarification'):
        answer=client.post('/api/v1/assistant/chat',json={'message':stt.json()['text']})
        report['assistant_status']=answer.status_code; report['assistant']=answer.json()
        if answer.status_code==200:
            speech=client.post('/api/v1/voice/synthesize',json={'text':answer.json()['answer'][:6000]})
            report['tts_status']=speech.status_code; report['tts_wav']=speech.content.startswith(b'RIFF')
            report['tts_elapsed_ms']=speech.headers.get('x-voice-elapsed-ms')
    report['end_to_end_ms']=round((time.perf_counter()-start)*1000)
    silent=bytearray(audio); silent[44:]=bytes(len(silent)-44)
    response=client.post('/api/v1/voice/transcribe',content=bytes(silent),headers={'Content-Type':'audio/wav'})
    report['silence']=response.json()
(ROOT/'work/phase3-probe.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if not k.startswith('reference_')},ensure_ascii=True,indent=2))
