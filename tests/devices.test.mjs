import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import vm from "node:vm";

const source = await readFile(new URL("../frontend/devices.js", import.meta.url), "utf8");

function element() {
  return {
    hidden: true, textContent: "", children: [], elements: {password: {value: "test password"}},
    append(...items) { this.children.push(...items); },
    replaceChildren() { this.children = []; },
    reset() { this.elements.password.value = ""; }
  };
}

test("el panel usa la sesión web y decide sobre la instalación seleccionada", async () => {
  const ids = new Map();
  const node = id => {
    if (!ids.has(id)) ids.set(id, element());
    return ids.get(id);
  };
  let logged = false;
  let state = "pending";
  const calls = [];
  async function fetch(path, options) {
    const endpoint = path.split("/").at(-1);
    const body = options.body && JSON.parse(options.body);
    calls.push([endpoint, body]);
    if (body) {
      assert.equal(options.headers["X-ITGlasses-Admin"], "1");
      assert.equal(options.credentials, "same-origin");
    }
    if (endpoint === "login") logged = true;
    if (!logged) return {ok:false, status:401};
    if (endpoint === "decision") state = "active";
    if (endpoint === "revoke") state = "revoked";
    if (endpoint === "logout") logged = false;
    const devices = state === "revoked" ? [] : [{id:"phone-12345", variant:"phone",code:"ABCD-2345",state}];
    return {ok:true, json:async () => ({devices})};
  }
  vm.runInNewContext(source, {document:{querySelector:node,createElement:element,createTextNode:x=>x},
    location:{protocol:"https:"}, fetch, clearInterval(){}, setInterval(){}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(node("#device-panel").hidden,false);
  await node("#device-login").onsubmit({preventDefault(){}});
  assert.equal(node("#device-login").elements.password.value,"");
  let row = node("#pairing-list").children[0];
  assert.match(row.children[0],/ABCD-2345/);
  assert.deepEqual(row.children.slice(1).map(x=>x.textContent),["Aprobar","Rechazar"]);
  await row.children[1].onclick();
  assert.deepEqual(calls.find(([action])=>action==="decision")[1],{id:"phone-12345",decision:"approve"});
  row = node("#pairing-list").children[0];
  assert.equal(row.children[1].textContent,"Revocar");
  await row.children[1].onclick();
  assert.equal(node("#pairing-list").children.length,0);
  await node("#device-logout").onclick();
  assert.equal(node("#device-management").hidden,true);
});

