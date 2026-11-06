import sys,json,time
from pathlib import Path
sys.path.insert(0,r'D:\Itglasses')
from backend.app.voice.worker import peak_memory
from backend.app.voice.transcription import normalize_transcript,_normalize_it_terms
start=time.perf_counter()
from faster_whisper import WhisperModel
task=json.load(sys.stdin)
t=time.perf_counter();model=WhisperModel(r'D:\Itglasses\work\whisper-human-model-base',device='cpu',compute_type='int8',cpu_threads=2,num_workers=1,local_files_only=True);loaded=time.perf_counter()
import wave,numpy as np
with wave.open(task['input'],'rb') as audio:
 assert (audio.getnchannels(),audio.getsampwidth(),audio.getframerate())==(1,2,16000)
 samples=np.frombuffer(audio.readframes(audio.getnframes()),dtype='<i2').astype(np.float32)/32768.0
segments,info=model.transcribe(samples,language='es',beam_size=5,temperature=0,condition_on_previous_text=False,vad_filter=False,word_timestamps=True)
ss=list(segments);raw=' '.join(s.text.strip() for s in ss)
result=dict(raw_text=raw,normalized_text=_normalize_it_terms(normalize_transcript(raw)),load_seconds=loaded-t,decode_seconds=time.perf_counter()-loaded,peak_working_set_mib=peak_memory(),segments=[dict(text=s.text,avg_logprob=s.avg_logprob,no_speech_prob=s.no_speech_prob,words=[dict(word=w.word,probability=w.probability) for w in (s.words or [])]) for s in ss])
print(json.dumps(result,ensure_ascii=False))
