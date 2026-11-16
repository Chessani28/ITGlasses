"""Isolated CPU experiment; does not import or select a production STT engine.

Run with project Python: python scripts/compare_stt.py
Requires work/whisper-eval/env and the pinned local model from the approved setup.
Results are local. No network inference or automatic model download is permitted.
"""
import argparse, ctypes, hashlib, io, json, math, os
from pathlib import Path
import statistics, subprocess, sys, time, urllib.request, wave

ROOT=Path(__file__).resolve().parents[1]
E=ROOT/'work/whisper-eval'
sys.path.insert(0,str(ROOT))

class Memory(ctypes.Structure):
    _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(x,ctypes.c_ulonglong) for x in ['total','available','page_total','page_available','virtual_total','virtual_available','extended']]

def available():
    m=Memory(); m.length=ctypes.sizeof(m)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)): raise RuntimeError('Memory query failed')
    return m.available

def guard():
    n=available()
    if n<3*2**30: raise RuntimeError(f'STOP: available RAM {n/2**30:.3f} GiB below 3 GiB')
    return n

def snapshot():
    result={'available_bytes':available()}
    result['gpu_used_mib']=subprocess.check_output(['nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits'],text=True).strip()
    try:
        with urllib.request.urlopen('http://127.0.0.1:11434/api/ps',timeout=3) as response:
            result['ollama_models']=[{k:m.get(k) for k in ['name','size','size_vram']} for m in json.load(response).get('models',[])]
    except Exception as exc: result['ollama_status_error']=type(exc).__name__
    return result

def whisper_worker(path):
    guard()
    from backend.app.voice.transcription import audio_quality
    from backend.app.voice.worker import peak_memory
    started=time.perf_counter(); cpu=time.process_time()
    with wave.open(str(path),'rb') as wav:
        if (wav.getnchannels(),wav.getsampwidth(),wav.getframerate(),wav.getcomptype()) != (1,2,16000,'NONE'):
            raise ValueError('Evaluation requires PCM16 mono 16 kHz WAV')
        pcm=wav.readframes(wav.getnframes())
    quality=audio_quality(pcm); quality['tail_rms']=audio_quality(pcm[-6400:])['rms']
    reasons=[]; details=[]; text=''; load_ms=0; decode_ms=0
    if quality['silence']:
        reasons=['no_speech']; compute_type=None
    else:
        from faster_whisper import WhisperModel
        model=WhisperModel(str(E/'model'),device='cpu',compute_type='int8',cpu_threads=2,num_workers=1,local_files_only=True)
        compute_type=model.model.compute_type
        load_ms=(time.perf_counter()-started)*1000
        decode=time.perf_counter()
        import numpy as np
        samples=np.frombuffer(pcm,dtype='<i2').astype(np.float32)/32768.0
        segments, info=model.transcribe(samples,language='es',beam_size=5,temperature=0,
            condition_on_previous_text=False,vad_filter=True,word_timestamps=False)
        for segment in segments:
            details.append({k:getattr(segment,k) for k in ['text','start','end','avg_logprob','no_speech_prob','compression_ratio']})
        text=' '.join(s['text'].strip() for s in details).strip()
        decode_ms=(time.perf_counter()-decode)*1000
        if not text: reasons.append('no_speech')
        if any(s['avg_logprob'] < -1 or s['compression_ratio']>2.4 or s['no_speech_prob']>0.6 for s in details):
            reasons.append('decoder_diagnostic')
        if quality['clipped_fraction']>0.01: reasons.append('clipped_audio')
        if quality['duration_seconds']>=27.9 and quality['tail_rms']>0.01: reasons.append('possibly_incomplete')
    return {'text':text,'needs_clarification':bool(reasons),'quality_reasons':reasons,'segments':details,
        'confidence_kind':None,'policy':'experimental decoder diagnostics, NOT calibrated confidence',
        'audio_quality':quality,'load_ms':round(load_ms),'decode_ms':round(decode_ms),
        'worker_elapsed_ms':round((time.perf_counter()-started)*1000),
        'cpu_seconds':round(time.process_time()-cpu,3),'peak_working_set_mib':peak_memory(),
        'device':'cpu','compute_type':compute_type}

def execute(engine,path):
    start_ram=guard(); start=time.perf_counter()
    env={**os.environ,'OMP_NUM_THREADS':'2','OPENBLAS_NUM_THREADS':'2','MKL_NUM_THREADS':'2',
         'HF_HUB_OFFLINE':'1','HF_HUB_DISABLE_TELEMETRY':'1','CUDA_VISIBLE_DEVICES':'-1',
         'PYTHONDONTWRITEBYTECODE':'1','PYTHONUTF8':'1'}
    if engine=='vosk':
        command=[str(ROOT/'.venv/Scripts/python.exe'),'-B','-m','backend.app.voice.worker']
        task=json.dumps({'kind':'stt','models':str(ROOT/'storage/voice/models'),'input':str(path)}).encode()
    else:
        command=[str(E/'env/Scripts/python.exe'),'-B',str(Path(__file__).resolve()),'--whisper-worker',str(path)]
        task=b''
    with (E/'worker-out.json').open('wb') as output, (E/'worker-err.txt').open('wb') as error:
        child=subprocess.Popen(command,cwd=ROOT,env=env,stdin=subprocess.PIPE,stdout=output,stderr=error,
            creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        try:
            child.stdin.write(task); child.stdin.close()
            minimum=start_ram
            while child.poll() is None:
                minimum=min(minimum,guard())
                if time.perf_counter()-start>90: raise RuntimeError('STOP: 90 second worker timeout')
                time.sleep(.1)
            if child.returncode: raise RuntimeError((E/'worker-err.txt').read_text(encoding='utf-8',errors='replace'))
        finally:
            if child.poll() is None: child.kill(); child.wait()
    result=json.loads((E/'worker-out.json').read_text(encoding='utf-8'))
    result.update(elapsed_ms=round((time.perf_counter()-start)*1000),available_before_bytes=start_ram,minimum_available_bytes=minimum)
    return result

def fingerprints():
    names=['backend/app/voice/worker.py','backend/app/voice/service.py','backend/app/voice/routes.py','config.toml','requirements-voice.txt']
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in names}

def main():
    guard()
    sys.path.insert(0,str(ROOT/'scripts'))
    from evaluate_speech import tokens, metrics
    manifest=json.loads((ROOT/'work/speech-eval/manifest.json').read_text(encoding='utf-8'))
    report={'model':'Whisper base multilingual','compute_type':'int8','cpu_threads':2,
        'settings':{'language':'es','beam_size':5,'temperature':0,'vad_filter':True,'condition_on_previous_text':False,'initial_prompt':None},
        'repetitions':3,'input_kind':'synthetic Piper es_ES, NOT human Mexican Spanish',
        'resources_before':snapshot(),'production_before':fingerprints(),'cases':[]}
    # Controls share the same PCM interface. Their text is not included in WER.
    controls=[]
    import random, array
    rng=random.Random(42)
    for name,pcm in [('silence',bytes(64000)),('noise',array.array('h',[rng.randint(-160,160) for _ in range(32000)]).tobytes())]:
        path=E/(name+'.wav')
        with wave.open(str(path),'wb') as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000);output.writeframes(pcm)
        controls.append({'path':str(path),'text':'','kind':name})
    try:
        for i,item in enumerate(manifest+controls):
            raw=Path(item['path']).read_bytes()
            row={**item,'sha256':hashlib.sha256(raw).hexdigest(),'runs':{'vosk':[],'whisper':[]}}
            for repetition in range(3):
                # Alternate order to reduce consistent order/cache bias.
                order=['vosk','whisper'] if (i+repetition)%2==0 else ['whisper','vosk']
                for engine in order: row['runs'][engine].append(execute(engine,Path(item['path'])))
            for engine in ['vosk','whisper']: row[engine]=row['runs'][engine][0]
            report['cases'].append(row)
            print(json.dumps({'case':i,'reference':item['text'],'vosk':row['vosk']['text'],'whisper':row['whisper']['text']},ensure_ascii=True),flush=True)
            (E/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        spoken=[x for x in report['cases'] if x['text']]
        summary={}
        for engine in ['vosk','whisper']:
            latencies=sorted(run['elapsed_ms'] for x in spoken for run in x['runs'][engine])
            correct=[x for x in spoken if tokens(x['text'])==tokens(x[engine]['text'])]
            summary[engine]={**metrics(spoken,engine),'exact_correct':len(correct),'total_spoken':len(spoken),
                'clarifications':sum(x[engine]['needs_clarification'] for x in spoken),
                'false_clarifications_exact':sum(x[engine]['needs_clarification'] for x in correct),
                'incorrect_accepted_exact':sum(not x[engine]['needs_clarification'] and tokens(x['text'])!=tokens(x[engine]['text']) for x in spoken),
                'latency_mean_ms':round(statistics.mean(latencies)),'latency_p50_ms':statistics.median(latencies),
                'latency_p95_ms':latencies[math.ceil(.95*len(latencies))-1],
                'peak_worker_mib':max(run['peak_working_set_mib'] for x in spoken for run in x['runs'][engine]),
                'minimum_system_available_gib':min(run['minimum_available_bytes'] for x in report['cases'] for run in x['runs'][engine])/2**30,
                'deterministic_across_repetitions':all(len({run['text'] for run in x['runs'][engine]})==1 for x in report['cases'])}
        report['summary']=summary
    except Exception as exc:
        report['stopped']=str(exc)
        raise
    finally:
        report['resources_after']=snapshot(); report['production_after']=fingerprints()
        report['production_unchanged']=report['production_before']==report['production_after']
        (E/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report['summary'],indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--whisper-worker'); args=parser.parse_args()
    if args.whisper_worker: print(json.dumps(whisper_worker(Path(args.whisper_worker))))
    else: main()
