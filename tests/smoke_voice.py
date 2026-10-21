"""Opt-in real speech probe. Never downloads models; uses synthetic speech only."""
import asyncio
from datetime import datetime
import io
import json
from pathlib import Path
import runpy
import sys
import wave

ROOT = Path('D:/ITGlasses')
sys.path.insert(0, str(ROOT))
import httpx
import numpy as np
from backend.app.voice.service import VoiceService


def pcm16k(raw):
    with wave.open(io.BytesIO(raw), 'rb') as source:
        rate = source.getframerate()
        assert source.getnchannels() == 1 and source.getsampwidth() == 2
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype='<i2')
    converted = np.interp(np.arange(int(len(samples) * 16000 / rate)) * rate / 16000,
                          np.arange(len(samples)), samples).astype('<i2')
    output = io.BytesIO()
    with wave.open(output, 'wb') as target:
        target.setnchannels(1); target.setsampwidth(2); target.setframerate(16000); target.writeframes(converted.tobytes())
    return output.getvalue()


async def main():
    voice = VoiceService()
    if not all(voice.status()[key] for key in ['stt_available','tts_available']):
        raise SystemExit('Approved speech models/dependencies are not installed. No downloads were attempted.')
    measure = runpy.run_path(str(ROOT / 'tests/smoke_knowledge.py'))['measure']
    baseline = measure()
    if baseline['free_ram_mib'] < 6144:
        raise SystemExit('Less than 6 GiB free RAM. Close optional applications before the combined real-model probe.')
    folder = ROOT / 'storage/voice/acceptance' / datetime.now().strftime('%Y%m%d-%H%M%S')
    folder.mkdir(parents=True, exist_ok=False)
    report = {'baseline':baseline, 'input_kind':'synthetic Piper speech, not a human microphone recording'}
    question = 'Explica brevemente qué es una dirección de internet.'
    audio = await voice.run('tts', {'text':question, 'speaker':0, 'speed':1})
    report['question_tts'] = {key:value for key,value in audio.items() if key != 'audio'}
    report['after_question_tts'] = measure()
    transcript = await voice.run('stt', pcm16k(audio['audio']))
    report['transcription'] = transcript
    assert transcript['text'].strip(), 'Empty real transcription'
    report['after_stt'] = measure()
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8000', trust_env=False, timeout=150) as client:
        status = (await client.get('/api/v1/status')).json()
        assert status['provider'] == 'ollama', 'The real probe must not use MockLLM'
        response = await client.post('/api/v1/chat', json={'message':transcript['text']})
        response.raise_for_status()
        answer = response.json()
        report['chat'] = {'seconds':response.elapsed.total_seconds(), 'response':answer}
        spoken = await client.post('/api/v1/voice/synthesize', json={'text':answer['answer']})
        spoken.raise_for_status()
        assert spoken.content.startswith(b'RIFF')
        (folder / 'synthetic-answer.wav').write_bytes(spoken.content)
        report['answer_tts'] = {'seconds':spoken.elapsed.total_seconds(), 'cpu_seconds':spoken.headers.get('x-voice-cpu-seconds'),
                              'peak_working_set_mib':spoken.headers.get('x-voice-peak-mib')}
    report['final'] = measure()
    (folder / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(folder / 'report.json')


if __name__ == '__main__': asyncio.run(main())
