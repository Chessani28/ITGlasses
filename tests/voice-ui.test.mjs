import test from 'node:test';
import assert from 'node:assert/strict';
import {initVoice} from '../frontend/voice-ui.mjs';

test('captured audio remains replayable after stopping microphone and uses separate element',async()=>{
  const previous={document:globalThis.document,window:globalThis.window,fetch:globalThis.fetch};
  const nodes=new Map();
  const element=id=>{
    if(!nodes.has(id)) nodes.set(id,{id,src:'',value:'',checked:false,plays:0,handlers:{},dataset:{},
      classList:{replace(){}},addEventListener(event,fn){this.handlers[event]=fn;},
      pause(){},load(){},removeAttribute(name){if(name==='src')this.src='';},
      async play(){this.plays++;}});
    return nodes.get(id);
  };
  globalThis.document={getElementById:element,addEventListener(){}};
  const windowEvents={}; globalThis.window={addEventListener:(event,fn)=>windowEvents[event]=fn};
  globalThis.fetch=async()=>new Response(JSON.stringify({text:'hola',raw_text:'hola',normalized_text:'hola',elapsed_ms:2}),{status:200,headers:{'Content-Type':'application/json'}});
  try {
    const controller=initVoice({lock(){},send(){}});
    await controller.io.transcribe(new Blob(['wav']),new AbortController().signal);
    controller.stop();
    await element('voice-playback-btn').handlers.click();
    assert.equal(element('voice-playback-audio').plays,1);
    assert.ok(element('voice-playback-audio').src.startsWith('blob:'));
    assert.equal(element('voice-audio').src,'');
    windowEvents.pagehide();
    assert.equal(element('voice-playback-audio').src,'');
  } finally {Object.assign(globalThis,previous);}
});
