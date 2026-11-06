import sys,json,time,wave
from pathlib import Path
sys.path.insert(0,r'D:\Itglasses')
from backend.app.voice.transcription import transcription_result,audio_quality
from vosk import Model,KaldiRecognizer,SetLogLevel
SetLogLevel(-1)
task=json.load(sys.stdin);t=time.perf_counter();model=Model(r'D:\Itglasses\storage\voice\models\vosk-model-small-es-0.42')
# Independently specified word-level vocabulary, never complete benchmark answers.
vocab='a al algo algún alguna algunos antes abrir actualizar ayuda ayudar ayudarme ahora ayer bien borrar buscar cambiar cerrar código cómo con conectar conecta conexión contraseña control copiar cuál cuando de del después debe deber realizar directorio dirección disco documento documentos dos el ella ellos en encender equipo error es esa ese eso esta este están está funciona funcionar gracias hacer hasta hay instalar internet impresora imprimir ip la las le leer lento lo los mantenimiento manual más me memoria mi mis mostrar muéstrame muy necesitar necesita no número o otra otro oye pantalla para paso pasos puedo puede puedes por porque primero problema programa que qué quién reciente red reparar reiniciar segundo según se servidor si sí sin sistema siguiente software son su sus te tengo tiene todos trabajo tres tu un una unas unos usar usuario versión ver wifi windows y yo cero uno cuatro cinco seis siete ocho nueve diez once cien doscientos trescientos cuatrocientos quinientos seiscientos setecientos ochocientos novecientos ve uve pe ene de ese hache ce i erre eme te u be vpn dns dhcp active directory http itglasses ram tcp https bios ssd usb cpu gpu archivo archivos seguridad permiso permisos navegador aplicación servicio cuenta datos empresa configuración acceso remoto virtual privada conexión cable router firewall outlook linux mac correo ping azul computadoras computadora configurar'.split()
probe={w:model.vosk_model_find_word(w) for w in ['vpn','dns','dhcp','active','directory','http','itglasses','no','ve','uve','pe','ene','hache','te','ip','ram','windows','[unk]']}
if task['mode']=='grammar':
 grammar=list(dict.fromkeys(w for w in vocab if model.vosk_model_find_word(w)>=0))+['[unk]'];rec=KaldiRecognizer(model,16000,json.dumps(grammar,ensure_ascii=False))
else:rec=KaldiRecognizer(model,16000)
rec.SetWords(True)
if task['mode']=='alternatives':rec.SetMaxAlternatives(5)
segments=[]
with wave.open(task['wav'],'rb') as a:
 pcm=a.readframes(a.getnframes());a.rewind()
 while chunk:=a.readframes(4000):
  if rec.AcceptWaveform(chunk):segments.append(json.loads(rec.Result()))
segments.append(json.loads(rec.FinalResult()))
parts=[];words=[]
for s in segments:
 top=s.get('alternatives',[s])[0];parts.append(top.get('text',''));words+=top.get('result',[])
q=audio_quality(pcm);q['tail_rms']=audio_quality(pcm[-6400:])['rms'];r=transcription_result(' '.join(x for x in parts if x),words,q)
r.update(segments=segments,vocabulary_probe=probe,inference_seconds=time.perf_counter()-t)
print(json.dumps(r,ensure_ascii=False))
