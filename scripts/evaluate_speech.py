"""Offline acoustic evaluation. No downloads. Synthetic audio is explicitly labelled.

Run: python scripts/evaluate_speech.py --synthetic --output work/speech-eval
For human audio: --manifest path.json (list of {path, text, kind: human}).
Baseline worker is optional and must be a trusted local script.
"""
import argparse
import asyncio
import ctypes
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import unicodedata
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.voice.service import VoiceService

PHRASES = ["¿Qué es una VPN?", "¿Cómo funciona una VPN?", "¿Qué es una dirección IP?", "¿Cómo configuro el DNS?", "¿Cómo actualizo la BIOS?", "¿Qué es un SSD NVMe?", "¿Cómo reviso la memoria RAM?", "¿Qué hago si Windows no arranca?", "Explícame el cuarto paso.", "¿Y qué pasa si eso no funciona?"]

def tokens(text):
    text = ''.join(c for c in unicodedata.normalize('NFD', text.casefold()) if unicodedata.category(c) != 'Mn')
    return re.findall(r'\w+', text)

def distance(a,b):
    row=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        new=[i]
        for j,y in enumerate(b,1): new.append(min(new[-1]+1,row[j]+1,row[j-1]+(x!=y)))
        row=new
    return row[-1]

def metrics(rows, key):
    word_errors=chars_errors=words=chars=acronyms=hits=0
    for row in rows:
        ref=tokens(row['text']); hyp=tokens(row[key]['text'])
        word_errors+=distance(ref,hyp); words+=len(ref)
        a=' '.join(ref); b=' '.join(hyp)
        chars_errors+=distance(a,b); chars+=len(a)
        for acronym in ['vpn','ip','dns','bios','ssd','nvme','ram','dhcp','cpu','gpu']:
            if acronym in ref:
                acronyms+=1; hits+=acronym in hyp
    return {'wer':word_errors/max(words,1),'cer':chars_errors/max(chars,1), 'acronym_hits':hits,'acronym_count':acronyms,'acronym_accuracy':hits/max(acronyms,1)}

def pcm16k(raw):
    import numpy as np
    with wave.open(io.BytesIO(raw),'rb') as source:
        rate=source.getframerate()
        samples=np.frombuffer(source.readframes(source.getnframes()),dtype='<i2')
    converted=np.interp(np.arange(int(len(samples)*16000/rate))*rate/16000,np.arange(len(samples)),samples).astype('<i2')
    out=io.BytesIO()
    with wave.open(out,'wb') as target:
        target.setnchannels(1); target.setsampwidth(2); target.setframerate(16000); target.writeframes(converted.tobytes())
    return out.getvalue()

def resources():
    class Memory(ctypes.Structure):
        _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(x,ctypes.c_ulonglong) for x in ['total','available','page_total','page_available','virtual_total','virtual_available','extended']]
    mem=Memory(); mem.length=ctypes.sizeof(mem); ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
    gpu=subprocess.check_output(['nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits'],text=True).strip()
    return {'free_ram_mib':round(mem.available/2**20,2),'gpu_used_mib':int(gpu)}

async def main(args):
    output=Path(args.output).resolve(); output.mkdir(parents=True,exist_ok=True)
    svc=VoiceService()
    if args.synthetic:
        manifest=[]
        for i,text in enumerate(PHRASES):
            path=output/f'synthetic-{i:02}.wav'
            if not path.exists():
                tts=await svc.run('tts',{'text':text,'speaker':0,'speed':1})
                path.write_bytes(pcm16k(tts['audio']))
            manifest.append({'text':text,'path':str(path),'kind':'synthetic Piper es_ES, NOT human Mexican Spanish'})
        (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    else:
        manifest=json.loads(Path(args.manifest).read_text(encoding='utf-8'))
    report={'resources_before':resources(),'cases':[]}
    for item in manifest:
        row=dict(item); raw=Path(item['path']).read_bytes()
        from backend.app.voice.service import validate_audio
        validate_audio(raw)
        if args.baseline_worker:
            task={'kind':'stt','models':str(svc.models),'input':item['path']}
            start=time.perf_counter()
            result=subprocess.run([sys.executable,'-B',args.baseline_worker],input=json.dumps(task),text=True,capture_output=True,check=True,timeout=90)
            row['baseline']=json.loads(result.stdout); row['baseline']['elapsed_ms']=round((time.perf_counter()-start)*1000)
        row['improved']=await svc.run('stt',raw)
        report['cases'].append(row)
        print(item['text'], '=>', row['improved']['text'],row['improved']['quality_reasons'],flush=True)
    report['improved_metrics']=metrics(report['cases'],'improved')
    if args.baseline_worker: report['baseline_metrics']=metrics(report['cases'],'baseline')
    report['resources_after']=resources()
    (output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='cases'},indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--synthetic',action='store_true'); group.add_argument('--manifest')
    parser.add_argument('--output',required=True); parser.add_argument('--baseline-worker')
    asyncio.run(main(parser.parse_args()))
