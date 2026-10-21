import test from "node:test";
import assert from "node:assert/strict";
import {api, chatPayload, documentState, MAX_FILE_BYTES, sourcePath, validateFile} from "../frontend/core.mjs";

test("general chat never sends invented history or knowledge identifiers", () => {
  assert.deepEqual(chatPayload("general", " hello ", "session", "choice"), {message:"hello"});
});
test("knowledge chat carries the existing conversation and explicit choice", () => {
  assert.deepEqual(chatPayload("knowledge", "follow up", "session", "choice"),
    {message:"follow up", conversation_id:"session", choice_document_id:"choice"});
});
test("new knowledge conversation does not fabricate an identifier", () => {
  assert.deepEqual(chatPayload("knowledge", "hello"), {message:"hello"});
});
test("chat validation honors each backend limit", () => {
  assert.throws(() => chatPayload("general", "x".repeat(4001)));
  assert.throws(() => chatPayload("knowledge", "x".repeat(16001)));
  assert.throws(() => chatPayload("general", " "));
  assert.equal(chatPayload("knowledge", "x".repeat(4001)).message.length, 4001);
});
test("file validation rejects unsupported, empty, missing and oversized input", () => {
  for (const file of [null, {name:"x.exe",size:1}, {name:"x.txt",size:0}, {name:"x.pdf",size:MAX_FILE_BYTES+1}]) {
    assert.throws(() => validateFile(file));
  }
  for (const extension of ["PDF","docx","txt","md"]) validateFile({name:`test.${extension}`, size:100});
});
test("source lookup cannot use server-provided URLs or arbitrary paths", () => {
  assert.throws(() => sourcePath("https://example.com/private"));
  assert.throws(() => sourcePath("../../config.toml"));
  assert.equal(sourcePath("00000000-0000-0000-0000-000000000000"), "/api/v1/knowledge/sources/00000000-0000-0000-0000-000000000000");
});
test("withdrawal takes priority over authorization in document status", () => {
  assert.equal(documentState({withdrawn:1,authorized:1}).label, "Retirado");
  assert.equal(documentState({withdrawn:0,authorized:0}).style, "warning");
});
test("request helper returns measured latency and parsed result", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response('{"answer":"safe"}', {status:200});
  try {
    const result = await api("/test");
    assert.equal(result.data.answer,"safe"); assert.ok(result.ms >= 0);
  } finally {globalThis.fetch=original;}
});
test("request helper preserves error code for expired conversation handling", async () => {
  const original=globalThis.fetch;
  globalThis.fetch=async()=>new Response('{"error":{"code":"conversation_expired","message":"Expired"}}',{status:409});
  try {await assert.rejects(api("/test"), error => error.code==="conversation_expired" && error.status===409);}
  finally{globalThis.fetch=original;}
});
test("malformed server output and network errors become clear failures", async () => {
  const original=globalThis.fetch;
  try {
    globalThis.fetch=async()=>new Response("<html>error</html>",{status:500});
    await assert.rejects(api("/test"), /respuesta no válida/);
    globalThis.fetch=async()=>{throw new TypeError("network");};
    await assert.rejects(api("/test"), /backend local/);
  } finally{globalThis.fetch=original;}
});
test("timeout does not claim that the backend operation was cancelled", async () => {
  const original=globalThis.fetch;
  globalThis.fetch=(_, {signal})=>new Promise((_,reject)=>{
    signal.addEventListener("abort",()=>reject(new DOMException("Abort","AbortError")));
  });
  try{await assert.rejects(api("/test",{},5), /servidor podría seguir procesando/);}
  finally{globalThis.fetch=original;}
});
