import test from "node:test";
import assert from "node:assert/strict";
import {parseInline, parseMarkdown, renderMarkdown} from "../frontend/markdown.mjs";

/** A tiny DOM: elements with tag, attributes, children and textContent. No innerHTML anywhere. */
function fakeDocument() {
  const make = tag => {
    const node = {tag, children: [], attributes: {}, append(...items) { this.children.push(...items); }};
    Object.defineProperty(node, "textContent", {
      get() { return this.text ?? this.children.map(child => child.textContent).join(""); },
      set(value) { this.text = String(value); this.children = []; },
    });
    for (const name of ["href", "target", "rel", "start"]) {
      Object.defineProperty(node, name, {get() { return this.attributes[name]; }, set(v) { this.attributes[name] = v; }});
    }
    return node;
  };
  return {
    createElement: make,
    createDocumentFragment: () => make("#fragment"),
    createTextNode: text => ({tag: "#text", textContent: text}),
  };
}
const tags = node => node.tag === "#text" ? [] : [node.tag, ...node.children.flatMap(tags)];

test("the answer of the web question renders its list and its bold words", () => {
  const text = "Un switch y un router son dispositivos de red distintos:\n\n- Un **switch** conecta dispositivos.\n"
    + "- Un **router** conecta múltiples redes.\n\nEn resumen: el switch maneja comunicación local.";
  const blocks = parseMarkdown(text);
  assert.deepEqual(blocks.map(block => block.type), ["p", "ul", "p"]);
  assert.deepEqual(blocks[1].items[0][0], [{type: "text", text: "Un "}, {type: "strong", text: "switch"},
    {type: "text", text: " conecta dispositivos."}]);
  const fragment = renderMarkdown(text, fakeDocument());
  assert.deepEqual(tags(fragment), ["#fragment", "p", "ul", "li", "strong", "li", "strong", "p"]);
  assert.equal(fragment.children[1].children[0].textContent, "Un switch conecta dispositivos.");
});

test("numbered steps keep their numbers and the citation stays literal", () => {
  const text = "«COMPUTER PREPARATION» (3 pasos):\n1. All ports communication\n2. Keyboard (For laptops)\n"
    + "3. BIOS battery status\n[Fuente: Training Setup Guideline, página 3, versión 1]";
  const blocks = parseMarkdown(text);
  assert.deepEqual(blocks.map(block => block.type), ["p", "ol", "p"]);
  assert.equal(blocks[1].start, 1);
  assert.equal(blocks[1].items.length, 3);
  assert.deepEqual(blocks[2].lines[0], [{type: "text", text: "[Fuente: Training Setup Guideline, página 3, versión 1]"}]);
  const later = renderMarkdown("4. Uno\n5. Dos", fakeDocument());
  assert.equal(later.children[0].tag, "ol");
  assert.equal(later.children[0].start, 4);
});

test("a wrapped list item stays in its item and lines of a paragraph keep their breaks", () => {
  const blocks = parseMarkdown("1. Abre el cliente VPN\n   y elige el perfil.\n2. Conecta.\n\nLínea uno\nLínea dos");
  assert.equal(blocks[0].items[0].length, 2);
  const fragment = renderMarkdown("Línea uno\nLínea dos", fakeDocument());
  assert.deepEqual(tags(fragment), ["#fragment", "p", "br"]);
});

test("only http and https links become links; everything else stays literal text", () => {
  assert.deepEqual(parseInline("[Microsoft Learn](https://learn.microsoft.com/x)"),
    [{type: "link", text: "Microsoft Learn", href: "https://learn.microsoft.com/x"}]);
  for (const unsafe of ["[clic](javascript:alert(1))", "[x](data:text/html,hola)", "[x](//evil.example)"]) {
    assert.deepEqual(parseInline(unsafe), [{type: "text", text: unsafe}]);
  }
  const link = renderMarkdown("Ver [fuente](https://example.org/a)", fakeDocument()).children[0].children[1];
  assert.deepEqual([link.tag, link.href, link.target, link.rel], ["a", "https://example.org/a", "_blank", "noopener noreferrer"]);
});

test("html in an answer is text, never markup", () => {
  const fragment = renderMarkdown("<img src=x onerror=alert(1)> **<b>hola</b>**", fakeDocument());
  assert.deepEqual(tags(fragment), ["#fragment", "p", "strong"]);
  assert.equal(fragment.textContent, "<img src=x onerror=alert(1)> <b>hola</b>");
});

test("italics, code, headings and stray asterisks", () => {
  assert.deepEqual(parseInline("usa `ipconfig /all` y *luego* reinicia"), [
    {type: "text", text: "usa "}, {type: "code", text: "ipconfig /all"}, {type: "text", text: " y "},
    {type: "em", text: "luego"}, {type: "text", text: " reinicia"}]);
  assert.deepEqual(parseInline("5 * 3 = 15"), [{type: "text", text: "5 * 3 = 15"}]);
  assert.deepEqual(parseMarkdown("## Resumen").map(block => block.type), ["h"]);
  assert.deepEqual(parseMarkdown(""), []);
  assert.deepEqual(parseMarkdown(null), []);
});
