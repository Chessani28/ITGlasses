import sys,json,re,time,statistics,csv,itertools,hashlib
from pathlib import Path
sys.path.insert(0,r'D:\Itglasses')
from scripts.evaluate_human_speech import align,tokens
out=Path(r'C:\Users\kikee\Documents\Codex\2026-11-05\itglasses-technical-audit-stabilization-and-intelligent\outputs')
def normextra(text):
 # Only exact explicit letter sequences, no homophone guesses, no number changes.
 for pattern,target in [(r'\bhache\s+te\s+te\s+pe\b','HTTP'),(r'\bhache\s+te\s+te\s+pe\s+ese\b','HTTPS'),(r'\bde\s+ene\s+es\b','DNS')]:
  text=re.sub(pattern,target,text,flags=re.I)
 return text
# Non-audio negative controls: ambiguous homophones and negations remain untouched.
controls=['no se conecta','se conecta','una vez','de aquí step','acep quinientos dos','HTTP 502','HTTP 512','oye y clases']
assert all(normextra(t)==t for t in controls)
assert normextra('hache te te pe 503')=='HTTP 503'
rows=json.loads((out/'vosk-candidates-results.json').read_text(encoding='utf-8'));assert len(rows)==60
base=[r for r in rows if r['mode']=='baseline'];extra=[]
for r in base:
 t=time.perf_counter();s=normextra(r['response']['normalized_text']);cost=time.perf_counter()-t
 extra.append(dict(r,mode='normalization',latency_seconds=r['latency_seconds']+cost,response={**r['response'],'normalized_text':s},normalized_wer=align(r['reference'],s)))
rows+=extra
terms=['vpn','ip','active directory','windows 11','ram','dns','dhcp','http 502']
def has(text,term):return ' '+term+' ' in ' '+' '.join(tokens(text))+' '
summary={}
for mode in ['baseline','alternatives','grammar','normalization']:
 rs=[r for r in rows if r['mode']==mode]
 s=dict(raw_wer=sum(sum(r['raw_wer'][k] for k in ['substitutions','deletions','insertions']) for r in rs)/126,normalized_wer=sum(sum(r['normalized_wer'][k] for k in ['substitutions','deletions','insertions']) for r in rs)/126,exact=sum(r['normalized_wer']['wer']==0 for r in rs),mean_latency=statistics.mean(r['latency_seconds'] for r in rs),max_latency=max(r['latency_seconds'] for r in rs))
 s['terms']={t:sum(has(r['reference'],t) and has(r['response']['normalized_text'],t) for r in rs) for t in terms};s['technical_accuracy']=sum(s['terms'].values())/10
 s['correct_phrase_regressions']=[r['reference'] for r in rs if r['normalized_wer']['wer']>0 and next(b for b in base if b['file']==r['file'])['normalized_wer']['wer']==0]
 s['negation_mismatches']=[dict(reference=r['reference'],hypothesis=r['response']['normalized_text']) for r in rs if has(r['reference'],'no')!=has(r['response']['normalized_text'],'no')]
 s['negations_preserved']=sum(has(r['response']['normalized_text'],'no') for r in rs if has(r['reference'],'no'))
 s['http_case']=[r['response']['normalized_text'] for r in rs if 'HTTP' in r['reference']]
 s['wake_case']=[r['response']['normalized_text'] for r in rs if 'ITGlasses' in r['reference']]
 summary[mode]=s
# Oracle is diagnostic only, uses references and MUST NOT become a candidate selector.
oracle=[]
for r in [r for r in rows if r['mode']=='alternatives']:
 options=[[a.get('text','') for a in s.get('alternatives',[s])] for s in r['response']['segments']]
 choices=list(itertools.product(*options));best=min((' '.join(x).strip() for x in choices),key=lambda x:align(r['reference'],x)['wer'])
 oracle.append(dict(reference=r['reference'],best_raw=best,raw_wer=align(r['reference'],best)['wer']))
summary['oracle_raw_wer_reference_assisted_NOT_DEPLOYABLE']=sum(round(r['raw_wer']*len(tokens(r['reference']))) for r in oracle)/126
# Detect ambiguity about negations in alternatives, diagnostic only.
summary['alternative_negation_disagreement']=[r['reference'] for r in rows if r['mode']=='alternatives' and any(len({has(a.get('text',''),'no') for a in s.get('alternatives',[s])})>1 for s in r['response']['segments'])]
(out/'vosk-candidates-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
cols=['Referencia','Candidato','Bruto','Normalizado','WER %','Latencia s','Aclaración','Negación igual a referencia']
with (out/'vosk-candidates-comparison.csv').open('w',encoding='utf-8-sig',newline='') as f:
 w=csv.writer(f);w.writerow(cols)
 for r in rows:w.writerow([r['reference'],r['mode'],r['response']['raw_text'],r['response']['normalized_text'],100*r['normalized_wer']['wer'],r['latency_seconds'],r['response']['needs_clarification'],has(r['reference'],'no')==has(r['response']['normalized_text'],'no')])
md='# Vosk: candidatos aislados para terminología técnica\n\n20 grabaciones humanas y referencias confirmadas, mismos WAV PCM16 mono 16 kHz. Tres decodificaciones reales por grabación (baseline, alternativas y gramática): 60 procesos, una pasada por candidato, CPU y dos hilos configurados. Normalización adicional se evalúa sobre el texto baseline, sin nueva inferencia. Sin modelos o dependencias nuevos; producción intacta. La latencia incluye proceso/importaciones/carga/decodificación; esta pasada no demuestra diferencias pequeñas de rendimiento ni estabilidad estadística. HTTP no aplica: workers aislados, no nueva integración de endpoint.\n\n## Capacidades comprobadas\n\nModelo small-es-0.42: HCLr.fst y Gr.fst presentes, gramática dinámica probada. La API instalada expone SetGrammar, SetMaxAlternatives y vosk_model_find_word. Índices encontrados: '+json.dumps(base[0]['response']['vocabulary_probe'],ensure_ascii=False)+'. Un índice -1 indica token ausente: no se añadió ninguna pronunciación. La gramática no incorpora vocabulario acústico nuevo. VPN/DNS/HTTP existen pero siguen pudiendo reconocerse mal; DHCP, directory e itglasses no existen como tokens exactos. No se probó un detector de activación real.\n\nEl worker productivo procesa bloques de 4000 muestras con SetWords(True), sin gramática ni alternativas. La normalización ya admite deletreo español de VPN/DNS/DHCP y Windows once, pero no reconstruye palabras ambiguas. La aclaración usa RMS/clipping/duración y confianza mínima: <0,65 es diagnóstico, <0,55 bloquea. No es detector fiable de negaciones o códigos erróneos. La captura web tiene prebuffer/silencio; las grabaciones externas no permiten atribuir a esa captura los inicios omitidos.\n\nFuentes oficiales: [gramática](https://raw.githubusercontent.com/alphacep/vosk-api/master/python/example/test_words.py), [alternativas](https://raw.githubusercontent.com/alphacep/vosk-api/master/python/example/test_alternatives.py), [implementación que omite términos fuera de vocabulario](https://raw.githubusercontent.com/alphacep/vosk-api/master/src/recognizer.cc). La API de ejemplo no prueba compatibilidad local por sí sola: aquí se verificó ejecutándola.\n\n## Candidatos\n\n- Baseline: decodificación actual, reproducida en el harness.\n- Alternativas: SetMaxAlternatives(5), conservar primera hipótesis por segmento, sin selección por referencia. Se guardan otras hipótesis como diagnóstico. Sus scores no son probabilidades calibradas.\n- Gramática: vocabulario manual de palabras comunes, letras, números y términos IT, filtrado al léxico disponible, más [unk]. Sin preguntas completas ni extracción de respuestas del manifiesto. Es restrictiva; [unk] no restablece el modelo de lenguaje general. Esta lista no pretende cubrir todo el español.\n- Normalización: secuencias explícitas hache-te-te-pe y de-ene-es, sin adivinar homófonos ni tocar negaciones/códigos. Ocho controles negativos de texto y un control positivo; no son pruebas acústicas nuevas.\n\n## Métricas\n\nWER por corpus de 126 palabras, ignorando acentos, mayúsculas y puntuación; sin equivalencias numéricas. Términos por las mismas diez apariciones del baseline.\n\n| Candidato | WER bruto % | WER normalizado % | Exactas /20 | Términos % | Negaciones /4 | Media s | Máxima s | Regresiones en exactas |\n|---|---:|---:|---:|---:|---:|---:|---:|---:|\n'
for mode in ['baseline','alternatives','grammar','normalization']:
 s=summary[mode];md+=f'| {mode} | {100*s["raw_wer"]:.2f} | {100*s["normalized_wer"]:.2f} | {s["exact"]} | {100*s["technical_accuracy"]:.0f} | {s["negations_preserved"]} | {s["mean_latency"]:.3f} | {s["max_latency"]:.3f} | {len(s["correct_phrase_regressions"])} |\n'
md+='\n## Por grabación: salida normalizada y WER\n\n| Referencia | Baseline | Alternativas top1 | Gramática |\n|---|---|---|---|\n'
for b in base:
 line=[b['reference']]
 for mode in ['baseline','alternatives','grammar']:
  r=next(r for r in rows if r['mode']==mode and r['file']==b['file']);line.append(r['response']['normalized_text']+f' ({100*r["normalized_wer"]["wer"]:.1f} %)')
 md+='| '+' | '.join(line)+' |\n'
md+='\nLa salida de normalización adicional y todos los textos brutos, latencias y aclaraciones constan en CSV. No usar una diferencia literal de WER para forzar números: «quinientos dos» conserva el número 502 aunque no sea igual por tokenización.\n\n## Resumen íntegro y diagnóstico de alternativas\n\n```json\n'+json.dumps(summary,ensure_ascii=False,indent=2)+'\n```\n\nEl oracle selecciona con la referencia conocida la mejor combinación de alternativas: es únicamente un techo diagnóstico retrospectivo, no una mejora automática ni una cifra desplegable. No se usó para producir las hipótesis de ningún candidato.\n'
(out/'vosk-candidates-report.md').write_text(md,encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
