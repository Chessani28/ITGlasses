import test from 'node:test';
import assert from 'node:assert/strict';
import {initVoice} from '../frontend/voice-ui.mjs';

test('captured audio remains replayable after stopping microphone and uses separate element',async()=>{
  const previous={document:globalThis.document,window:globalThis.window,fetch:globalThis.fetch};
  const nodes=new Map();
  const element=id=>{
    if(!nodes.has(id)) nodes.set(id,{id,src:'',value:id==='voice-engine'?'whisper-small':'',checked:false,plays:0,handlers:{},dataset:{},
      classList:{replace(){}},addEventListener(event,fn){this.handlers[event]=fn;},
      removeEventListener(event,fn){if(this.handlers[event]===fn)delete this.handlers[event];},
      focus(){},
      pause(){},load(){},removeAttribute(name){if(name==='src')this.src='';},
      async play(){this.plays++;}});
    return nodes.get(id);
  };
  globalThis.document={getElementById:element,addEventListener(){}};
  const windowEvents={}; globalThis.window={addEventListener:(event,fn)=>windowEvents[event]=fn};
  const requests=[];
  globalThis.fetch=async(_path,options={})=>{
    requests.push(options);
    return new Response(JSON.stringify({text:'hola',raw_text:'hola',normalized_text:'hola',elapsed_ms:2,engine:'whisper-small'}),{status:200,headers:{'Content-Type':'application/json'}});
  };
  try {
    const controller=initVoice({lock(){},send(){}});
    await controller.io.transcribe(new Blob(['wav']),new AbortController().signal,'whisper-small');
    assert.equal(requests.at(-1).headers['X-ITGlasses-STT-Engine'],'whisper-small');
    assert.equal(element('voice-engine-active').textContent,'Whisper Small · experimental');
    controller.stop();
    await element('voice-playback-btn').handlers.click();
    assert.equal(element('voice-playback-audio').plays,1);
    assert.ok(element('voice-playback-audio').src.startsWith('blob:'));
    assert.equal(element('voice-audio').src,'');
    windowEvents.pagehide();
    assert.equal(element('voice-playback-audio').src,'');
  } finally {Object.assign(globalThis,previous);}
});

test('voice UI refuses to display a different engine than the one requested',async()=>{
  const previous={document:globalThis.document,window:globalThis.window,fetch:globalThis.fetch};
  const nodes=new Map();
  const element=id=>{
    if(!nodes.has(id)) nodes.set(id,{id,src:'',value:'whisper-small',checked:false,handlers:{},dataset:{},textContent:'',
      classList:{replace(){}},addEventListener(event,fn){this.handlers[event]=fn;},
      removeEventListener(event,fn){if(this.handlers[event]===fn)delete this.handlers[event];},
      focus(){},pause(){},load(){},removeAttribute(){},async play(){}});
    return nodes.get(id);
  };
  globalThis.document={getElementById:element,addEventListener(){}};
  globalThis.window={addEventListener(){}};
  globalThis.fetch=async()=>new Response(JSON.stringify({
    text:'untrusted fallback',raw_text:'untrusted fallback',normalized_text:'untrusted fallback',
    elapsed_ms:2,engine:'vosk'
  }),{status:200,headers:{'Content-Type':'application/json'}});
  try {
    const controller=initVoice({lock(){},send(){}});
    await assert.rejects(
      controller.io.transcribe(new Blob(['wav']),new AbortController().signal,'whisper-small'),
      /devolvió Vosk en lugar de Whisper Small/
    );
    assert.equal(element('voice-engine-active').textContent,'');
    assert.equal(element('voice-transcript').textContent,'');
  } finally {Object.assign(globalThis,previous);}
});
