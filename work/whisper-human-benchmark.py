import sys,os,json,time,subprocess,hashlib,ctypes
from pathlib import Path
root=Path(r'D:\Itglasses');out=Path(r'C:\Users\kikee\Documents\Codex\2026-11-05\itglasses-technical-audit-stabilization-and-intelligent\outputs')
sys.path.insert(0,str(root))
from scripts.evaluate_human_speech import align
from scripts.evaluate_speech import resources
setup=json.loads((root/'work/whisper-eval/setup.json').read_text(encoding='utf-8'))
for f in setup['files']:
 p=root/'work/whisper-human-model-base'/f['name'];assert hashlib.sha256(p.read_bytes()).hexdigest()==f['sha256']
manifest=json.loads((out/'ffmpeg-conversion-manifest.json').read_text(encoding='utf-8'));refs={r['file']:r['reference'] for r in json.loads((out/'human-voice-ffmpeg-results.json').read_text(encoding='utf-8'))}
report=dict(model_hashes_verified=True,resources_before=resources(),cases=[])
env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','OMP_NUM_THREADS':'2','OPENBLAS_NUM_THREADS':'2','PYTHONDONTWRITEBYTECODE':'1','PYTHONIOENCODING':'utf-8'}
for rep in range(3):
 for i,item in enumerate(manifest):
  assert hashlib.sha256((root/'work/voice-human-eval'/item['file']).read_bytes()).hexdigest()==item['sha256']
  for engine in (['vosk','whisper'] if (rep+i)%2==0 else ['whisper','vosk']):
   before=resources();assert before['free_ram_mib']>=1500
   cmd=[str(root/'.venv/Scripts/python.exe'),'-B','-m','backend.app.voice.worker'] if engine=='vosk' else [str(root/'work/whisper-human-env/Scripts/python.exe'),'-B',str(root/'work/whisper-human-worker.py')]
   task={'kind':'stt','input':item['wav'],'models':str(root/'storage/voice/models')}
   start=time.perf_counter()
   try:
    p=subprocess.run(cmd,input=json.dumps(task),capture_output=True,text=True,encoding='utf-8',env=env,cwd=root,timeout=90)
    elapsed=time.perf_counter()-start
    payload=json.loads(p.stdout);assert p.returncode==0,payload
    ref=refs[item['file']];row=dict(engine=engine,repetition=rep+1,file=item['file'],reference=ref,elapsed_seconds=elapsed,response=payload,raw_wer=align(ref,payload['raw_text']),normalized_wer=align(ref,payload['normalized_text']),resources_before=before,resources_after=resources())
   except Exception as e: row=dict(engine=engine,repetition=rep+1,file=item['file'],error=type(e).__name__,elapsed_seconds=time.perf_counter()-start)
   report['cases'].append(row);(out/'whisper-human-benchmark.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
   print(rep+1,i+1,engine,row.get('error','OK'),round(row['elapsed_seconds'],3),flush=True)
report['resources_after']=resources();(out/'whisper-human-benchmark.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
