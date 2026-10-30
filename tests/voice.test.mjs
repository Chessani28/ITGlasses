import test from 'node:test';
import assert from 'node:assert/strict';
import {VoiceController, wakeQuestion, spokenAnswer, microphoneError, normalizeProcedureNumbers} from '../frontend/voice-core.mjs';
import {Segmenter, pcmWav} from '../frontend/microphone.mjs';

function setup(overrides = {}) {
  const events = [], calls = []; let timeout;
  const io = {state:(...args)=>events.push(args), stopAudio:()=>{}, capture:async()=>({stop:()=>calls.push('stop')}),
    transcript:text=>calls.push(text), transcribe:async()=>({text:'oye itglasses qué es DNS'}),
    chat:async text=>{calls.push(['chat',text]); return {answer:'DNS',sources:[]};}, speak:async()=>calls.push('speak'), ...overrides};
  const controller = new VoiceController(io, {setTimeout:fn=>{timeout=fn;return 1;},clearTimeout:()=>{timeout=null;}});
  return {controller, events, calls, expire:()=>timeout?.()};
}
const preferences = {wake:true,speak:true,windowSeconds:15,aliases:['oye itglasses']};

test('wake prefix matching is explicit, normalized and does not trigger in the middle',()=>{
  assert.equal(wakeQuestion('¡Oye, ITGlasses!'), '');
  assert.equal(wakeQuestion('Oye ITGlasses qué es DNS'), 'qué es DNS');
  assert.equal(wakeQuestion('No digas oye ITGlasses'), null);
  assert.equal(wakeQuestion('oye itglassesx'), null);
  assert.equal(wakeQuestion('hola soporte', ['hola soporte']), '');
});
test('speech preserves ambiguity and labels general knowledge',()=>{
  assert.match(spokenAnswer({answer:'¿Cuál de los tres procedimientos?', knowledge_type:'documented_evidence', sources:[]}),/Cuál de los tres/);
  assert.match(spokenAnswer({answer:'DNS'}),/no una política/);
  assert.throws(()=>spokenAnswer({answer:'x'.repeat(6000)}),/límite/);
});
test('wake then follow-up uses the same chat adapter and returns to wake on timeout',async()=>{
  let text='oye itglasses qué es DNS';
  const {controller,calls,expire}=setup({transcribe:async()=>({text})});
  await controller.start(preferences); assert.equal(controller.phase,'idle');
  await controller.utterance('audio'); assert.equal(controller.phase,'listening');
  text='el de las computadoras'; await controller.utterance('audio');
  assert.deepEqual(calls.filter(x=>Array.isArray(x)),[['chat','qué es DNS'],['chat','el de las computadoras']]);
  expire(); assert.equal(controller.waitingWake,true);
});
test('non-wake speech never calls chat when armed',async()=>{
  const {controller,calls}=setup({transcribe:async()=>({text:'conversación ajena'})});
  await controller.start(preferences); await controller.utterance('audio');
  assert.equal(calls.some(x=>Array.isArray(x)),false); assert.equal(controller.phase,'idle');
});
test('manual listening closes the microphone on timeout',async()=>{
  const {controller,calls,expire}=setup(); await controller.start({...preferences,wake:false}); expire();
  assert.equal(controller.active,false); assert.ok(calls.includes('stop'));
});
test('permission failures are visible and inactive',async()=>{
  const {controller,events}=setup({capture:async()=>{throw {name:'NotAllowedError'};}});
  await controller.start(preferences); assert.equal(controller.active,false);
  assert.equal(controller.phase,'error'); assert.match(events.at(-1)[1],/denegado/);
  assert.match(microphoneError({name:'NotFoundError'}),/No hay/);
});
test('stop while permission is pending releases a late stream',async()=>{
  let resolve,stopped=false; const {controller}=setup({capture:()=>new Promise(r=>resolve=r)});
  const pending=controller.start(preferences); controller.stop(); resolve({stop:()=>{stopped=true;}}); await pending;
  assert.ok(stopped); assert.equal(controller.active,false);
});
test('stop during transcription prevents stale chat and speech',async()=>{
  let resolve; const {controller,calls}=setup({transcribe:()=>new Promise(r=>resolve=r)});
  await controller.start(preferences); const pending=controller.utterance('audio'); controller.stop(); resolve({text:'oye itglasses pregunta'}); await pending;
  assert.equal(calls.some(x=>Array.isArray(x)),false); assert.equal(controller.phase,'idle');
});
test('stop during chat prevents stale playback',async()=>{
  let resolve; const {controller,calls}=setup({chat:()=>new Promise(r=>resolve=r)});
  await controller.start(preferences); const pending=controller.utterance('audio'); await new Promise(r=>setImmediate(r));
  controller.stop(); resolve({answer:'stale'}); await pending; assert.ok(!calls.includes('speak'));
});
test('speech errors release microphone and preserve visible error',async()=>{
  const {controller,events}=setup({speak:async()=>{throw new Error('source unavailable');}});
  await controller.start(preferences); await controller.utterance('audio');
  assert.equal(controller.phase,'error'); assert.equal(controller.active,false); assert.equal(events.at(-1)[1],'source unavailable');
});
test('PCM conversion is mono 16 kHz with bounded signed samples',async()=>{
  const blob=pcmWav([new Float32Array(4800).fill(0.5)],48000), view=new DataView(await blob.arrayBuffer());
  assert.equal(view.getUint32(24,true),16000); assert.equal(view.getUint16(22,true),1); assert.equal(blob.size,3244);
});
test('silence segmentation rejects background silence and emits speech once',()=>{
  const clips=[], segment=new Segmenter(16000,x=>clips.push(x));
  for(let i=0;i<30;i++) segment.push(new Float32Array(1600)); assert.equal(clips.length,0);
  for(let i=0;i<5;i++) segment.push(new Float32Array(1600).fill(.1));
  for(let i=0;i<12;i++) segment.push(new Float32Array(1600)); assert.equal(clips.length,1);
});
test('speech activity renews the follow-up window instead of expiring mid-sentence',async()=>{
  let renewals=0;
  const controller=new VoiceController(setup().controller.io,{setTimeout:()=>++renewals,clearTimeout:()=>{}});
  await controller.start({...preferences,wake:false}); const initial=renewals;
  controller.activity(); assert.equal(renewals,initial+1);
  controller.stop(); controller.activity(); assert.equal(renewals,initial+1);
});

test('observed synthetic wake alias is explicit and technical identifiers remain intact',()=>{
  assert.equal(wakeQuestion('oye y te clases'), '');
  assert.equal(wakeQuestion('Oye ITGlasses consulta IT-24 en 192.168.1.1'), 'consulta IT-24 en 192.168.1.1');
});
test('spoken procedure numbers normalize narrowly without inventing large identifiers',()=>{
  assert.equal(normalizeProcedureNumbers('cuál es el procedimiento veinticuatro'), 'cuál es el procedimiento 24');
  assert.equal(normalizeProcedureNumbers('procedimiento número treinta y dos de redes'), 'procedimiento número 32 de redes');
  assert.equal(normalizeProcedureNumbers('procedimiento veinticuatro mil'), 'procedimiento veinticuatro mil');
  assert.equal(normalizeProcedureNumbers('hay veinticuatro computadoras'), 'hay veinticuatro computadoras');
});

test('uncertain STT asks for repetition without calling chat or TTS',async()=>{
  const {controller,calls,events}=setup({transcribe:async()=>({text:'otra cosa',needs_clarification:true,clarification:'Repite la pregunta'})});
  await controller.start({...preferences,wake:false}); await controller.utterance('audio');
  assert.equal(calls.some(x=>Array.isArray(x)),false);
  assert.ok(!calls.includes('speak')); assert.equal(events.at(-1)[1],'Repite la pregunta');
});
test('length-limited capture is never sent to transcription',async()=>{
  let sent=false;
  const {controller}=setup({transcribe:async()=>{sent=true; return {text:'incompleta'};}});
  await controller.start({...preferences,wake:false}); await controller.utterance({possiblyIncomplete:true});
  assert.equal(sent,false);
});
