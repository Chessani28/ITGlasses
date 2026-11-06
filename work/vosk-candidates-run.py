import sys,os,json,subprocess,time,hashlib
from pathlib import Path
sys.path.insert(0,r'D:\Itglasses')
from scripts.evaluate_human_speech import align
root=Path(r'D:\Itglasses');out=Path(r'C:\Users\kikee\Documents\Codex\2026-11-05\itglasses-technical-audit-stabilization-and-intelligent\outputs')
manifest=json.loads((out/'ffmpeg-conversion-manifest.json').read_text(encoding='utf-8'));refs={r['file']:r['reference'] for r in json.loads((out/'human-voice-ffmpeg-results.json').read_text(encoding='utf-8'))}
env={**os.environ,'OMP_NUM_THREADS':'2','OPENBLAS_NUM_THREADS':'2','PYTHONIOENCODING':'utf-8','PYTHONDONTWRITEBYTECODE':'1'}
rows=[]
for item in manifest:
 assert hashlib.sha256((root/'work/voice-human-eval'/item['file']).read_bytes()).hexdigest()==item['sha256']
 for mode in ['baseline','alternatives','grammar']:
  t=time.perf_counter();p=subprocess.run([str(root/'.venv/Scripts/python.exe'),'-B',str(root/'work/vosk-candidate-worker.py')],input=json.dumps({'wav':item['wav'],'mode':mode}),text=True,encoding='utf-8',capture_output=True,cwd=root,env=env,timeout=90);elapsed=time.perf_counter()-t
  assert p.returncode==0,p.stderr
  r=json.loads(p.stdout);ref=refs[item['file']];rows.append(dict(file=item['file'],reference=ref,mode=mode,latency_seconds=elapsed,response=r,raw_wer=align(ref,r['raw_text']),normalized_wer=align(ref,r['normalized_text'])))
  (out/'vosk-candidates-results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8');print(len(rows),mode,round(elapsed,3),flush=True)
