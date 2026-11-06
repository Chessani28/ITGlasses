import json,csv,statistics,collections,sys
from pathlib import Path
root=Path(r'D:\Itglasses');sys.path.insert(0,str(root))
from scripts.evaluate_human_speech import tokens
out=Path(r'C:\Users\kikee\Documents\Codex\2026-11-05\itglasses-technical-audit-stabilization-and-intelligent\outputs')
def has(text,term):return ' '+term+' ' in ' '+' '.join(tokens(text))+' '
terms=['vpn','ip','active directory','windows 11','ram','dns','dhcp','http 502']
def build():
 data=json.loads((out/'whisper-human-benchmark.json').read_text(encoding='utf-8'));rows=data['cases'];assert len(rows)==120 and not any('error' in r for r in rows)
 summary={};first={}
 for engine in ['vosk','whisper']:
  allrows=[r for r in rows if r['engine']==engine];rs=[r for r in allrows if r['repetition']==1];first[engine]=rs
  s={'count':len(allrows),'unique_recordings':len(rs),'latency_mean_seconds':statistics.mean(r['elapsed_seconds'] for r in allrows),'latency_max_seconds':max(r['elapsed_seconds'] for r in allrows),'peak_ram_mib':max(r['response']['peak_working_set_mib'] for r in allrows)}
  for key in ['raw_wer','normalized_wer']:
   s[key]=sum(sum(r[key][k] for k in ['substitutions','deletions','insertions']) for r in rs)/sum(r[key]['reference_words'] for r in rs)
  s['exact']=sum(r['normalized_wer']['wer']==0 for r in rs);s['raw_exact']=sum(r['raw_wer']['wer']==0 for r in rs)
  s['terms']={t:{'expected':sum(has(r['reference'],t) for r in rs),'correct':sum(has(r['reference'],t) and has(r['response']['normalized_text'],t) for r in rs)} for t in terms}
  s['technical_accuracy']=sum(v['correct'] for v in s['terms'].values())/sum(v['expected'] for v in s['terms'].values())
  s['deterministic']=all(len({r['response']['raw_text'] for r in allrows if r['file']==a['file']})==1 for a in rs)
  s['negations']=[{'reference':r['reference'],'hypothesis':r['response']['normalized_text'],'preserved':has(r['response']['normalized_text'],'no')} for r in rs if has(r['reference'],'no')]
  s['insertions']=sum(r['normalized_wer']['insertions'] for r in rs);s['deletions']=sum(r['normalized_wer']['deletions'] for r in rs)
  if engine=='whisper':s.update(load_mean_seconds=statistics.mean(r['response']['load_seconds'] for r in allrows),decode_mean_seconds=statistics.mean(r['response']['decode_seconds'] for r in allrows))
  summary[engine]=s
 summary['minimum_ram_available_mib']=min(r[k]['free_ram_mib'] for r in rows for k in ['resources_before','resources_after'])
 summary['gpu_used_mib_range']=[min(r[k]['gpu_used_mib'] for r in rows for k in ['resources_before','resources_after']),max(r[k]['gpu_used_mib'] for r in rows for k in ['resources_before','resources_after'])]
 (out/'whisper-human-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
 cols=['Archivo','Referencia','Motor','Bruto','Normalizado','WER bruto %','WER normalizado %','Términos','Latencia media s (3)','Máxima s (3)','Pico RAM MiB (3)','Confianza media','Aclaración']
 table=[]
 for a in first['vosk']:
  for engine in ['vosk','whisper']:
   r=next(x for x in first[engine] if x['file']==a['file']);p=r['response'];repeat=[x for x in rows if x['engine']==engine and x['file']==r['file']]
   scores=[w['conf'] for w in p['words']] if engine=='vosk' else [w['probability'] for s in p['segments'] for w in s['words']]
   ts=[t+(' ✓' if has(p['normalized_text'],t) else ' ✗') for t in terms if has(r['reference'],t)]
   table.append([r['file'],r['reference'],engine,p['raw_text'],p['normalized_text'],round(100*r['raw_wer']['wer'],2),round(100*r['normalized_wer']['wer'],2),'; '.join(ts),round(statistics.mean(x['elapsed_seconds'] for x in repeat),3),round(max(x['elapsed_seconds'] for x in repeat),3),max(x['response']['peak_working_set_mib'] for x in repeat),round(statistics.mean(scores),3) if scores else None,str(p['needs_clarification']) if engine=='vosk' else 'N/A: sin política adaptada'])
 with (out/'whisper-human-comparison.csv').open('w',encoding='utf-8-sig',newline='') as f:w=csv.writer(f);w.writerow(cols);w.writerows(table)
 md='# Benchmark humano: Vosk vs faster-whisper Base\n\n20 grabaciones humanas de un hablante, micrófono de laptop, referencias confirmadas por nombres; 3 repeticiones por motor, 120 procesos completados. Los originales M4A y la configuración de producción no se modificaron.\n\n## Integridad y método\n\nPesos Base multilingües verificados por SHA-256 contra setup.json, revisión ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66. faster-whisper 1.2.1, CTranslate2 4.8.4; entorno aislado Python 3.14.7. CPU int8, dos hilos, un worker; idioma es, beam 5, temperatura 0, sin VAD, sin prompt/hotwords ni contexto entre archivos. Red deshabilitada mediante modo offline, modelo por ruta local. Vosk usa el worker existente real y modelo small-es-0.42, con las mismas variables de hilos. Misma normalización textual de producción para ambos, sin cambiarla.\n\nLa combinación instalada de PyAV y faster-whisper falla al abrir archivos: TypeError por metadata_errors. Se pasó a Whisper el PCM float32 derivado sin resampling de los mismos WAV PCM16/16 kHz/mono usando wave+NumPy. No se instaló ni corrigió ningún paquete. El benchmark mide inferencia, no demuestra compatibilidad de apertura directa ni integración de Whisper en API.\n\nLos procesos se ejecutaron secuencialmente en orden alternado. Cada archivo carga su modelo en proceso nuevo, como el worker Vosk. Latencia del padre incluye arranque, importaciones, carga, inferencia y salida; no incluye grabación. No hubo prueba de servicio persistente. HTTP no aplica a este benchmark de workers; los 20 HTTP 200 del baseline anterior no se atribuyen a Whisper. Pico RAM es PeakWorkingSetSize del worker; el launcher de Python y padre no están sumados. GPU se muestreó antes/después, sin captura de picos intermedios ni atribución por proceso; Whisper fue forzado a CPU.\n\nWER de corpus ignora acentos, mayúsculas y puntuación; sin equivalencias numéricas ni deletreo. Las métricas de calidad usan 20 frases, no 60 hablantes; repeticiones solo para latencia y determinismo. Inserciones son indicadores de errores, no alucinaciones auditivamente confirmadas. No hubo revisión auditiva independiente.\n\n## Comparación\n\n| Métrica | Vosk | Whisper Base CPU int8 |\n|---|---:|---:|\n'
 for label,key,scale in [('WER bruto','raw_wer',100),('WER normalizado','normalized_wer',100),('Frases exactas /20','exact',1),('Términos correctos %','technical_accuracy',100),('Latencia media s','latency_mean_seconds',1),('Latencia máxima s','latency_max_seconds',1),('Pico RAM worker MiB','peak_ram_mib',1),('Inserciones normalizadas','insertions',1),('Omisiones normalizadas','deletions',1)]:md+=f'| {label} | {summary["vosk"][key]*scale:.3f} | {summary["whisper"][key]*scale:.3f} |\n'
 md+='\n## Por grabación\n\nLa tabla CSV adjunta incluye además bruto, términos, confianza, latencias por archivo y memoria.\n\n| Referencia | Vosk normalizado | WER % | Whisper bruto | Whisper normalizado | WER % |\n|---|---|---:|---|---|---:|\n'
 for a in first['vosk']:
  b=next(x for x in first['whisper'] if x['file']==a['file']);md+='| '+' | '.join([a['reference'],a['response']['normalized_text'],f'{100*a["normalized_wer"]["wer"]:.1f}',b['response']['raw_text'],b['response']['normalized_text'],f'{100*b["normalized_wer"]["wer"]:.1f}'])+' |\n'
 md+='\n## Datos completos de resumen\n\n```json\n'+json.dumps(summary,ensure_ascii=False,indent=2)+'\n```\n'
 (out/'whisper-human-comparison.md').write_text(md,encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':build()
