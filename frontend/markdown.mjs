/** Minimal Markdown for the assistant's answers, built with createElement/textContent only (never parsed as HTML).
 *
 * Supported: paragraphs (blank line), line breaks, bulleted lists (-, *, •), numbered lists that keep their first
 * number (1. or 1)), "# heading" lines (bold paragraph), **bold**, *italic*, `code` and [text](https://…) links
 * (http and https only; anything else stays literal text). Everything else is shown as the literal text. */

const BULLET = /^\s*[-*•]\s+(.*)$/;
const NUMBERED = /^\s*(\d{1,3})[.)]\s+(.*)$/;
const HEADING = /^\s*#{1,3}\s+(.*)$/;
const CONTINUATION = /^\s{2,}\S/;
const INLINE = /\*\*([^*\n]+?)\*\*|`([^`\n]+)`|\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)|(?<![\w*])\*([^*\s][^*\n]*?)\*(?![\w*])/g;

/** Inline spans of one line: [{type: "text"|"strong"|"em"|"code"|"link", text, href?}]. */
export function parseInline(line) {
  const spans = [];
  let last = 0;
  for (const match of String(line).matchAll(INLINE)) {
    if (match.index > last) spans.push({type: "text", text: line.slice(last, match.index)});
    if (match[1] !== undefined) spans.push({type: "strong", text: match[1]});
    else if (match[2] !== undefined) spans.push({type: "code", text: match[2]});
    else if (match[3] !== undefined) spans.push({type: "link", text: match[3], href: match[4]});
    else spans.push({type: "em", text: match[5]});
    last = match.index + match[0].length;
  }
  if (last < line.length) spans.push({type: "text", text: line.slice(last)});
  return spans;
}

/** Blocks of a text: [{type: "p", lines}, {type: "h", lines}, {type: "ul"|"ol", start?, items: [lines]}]; each line
 * is a list of inline spans. */
export function parseMarkdown(text) {
  const blocks = [];
  let current = null;
  const close = () => { current = null; };
  for (const raw of String(text ?? "").split(/\r?\n/)) {
    if (!raw.trim()) { close(); continue; }
    const bullet = raw.match(BULLET), numbered = raw.match(NUMBERED), heading = raw.match(HEADING);
    if (bullet || numbered) {
      const type = bullet ? "ul" : "ol";
      if (!current || current.type !== type) {
        current = {type, items: []};
        if (type === "ol") current.start = Number(numbered[1]);
        blocks.push(current);
      }
      current.items.push([parseInline(bullet ? bullet[1] : numbered[2])]);
    } else if (current && (current.type === "ul" || current.type === "ol") && CONTINUATION.test(raw)) {
      current.items[current.items.length - 1].push(parseInline(raw.trim()));   // an item that wraps
    } else if (heading) {
      blocks.push({type: "h", lines: [parseInline(heading[1])]});
      close();
    } else {
      if (!current || current.type !== "p") { current = {type: "p", lines: []}; blocks.push(current); }
      current.lines.push(parseInline(raw));
    }
  }
  return blocks;
}

function appendLines(doc, parent, lines) {
  lines.forEach((spans, index) => {
    if (index) parent.append(doc.createElement("br"));
    for (const span of spans) {
      if (span.type === "text") { parent.append(doc.createTextNode(span.text)); continue; }
      const tag = {strong: "strong", em: "em", code: "code", link: "a"}[span.type];
      const element = doc.createElement(tag);
      element.textContent = span.text;
      if (span.type === "link") {
        element.href = span.href;
        element.target = "_blank";
        element.rel = "noopener noreferrer";
      }
      parent.append(element);
    }
  });
}

/** A DocumentFragment with the answer: only elements and text nodes, never parsed HTML. */
export function renderMarkdown(text, doc = globalThis.document) {
  const fragment = doc.createDocumentFragment();
  for (const block of parseMarkdown(text)) {
    if (block.type === "ul" || block.type === "ol") {
      const list = doc.createElement(block.type);
      if (block.type === "ol" && block.start !== 1) list.start = block.start;
      for (const item of block.items) {
        const li = doc.createElement("li");
        appendLines(doc, li, item);
        list.append(li);
      }
      fragment.append(list);
    } else {
      const paragraph = doc.createElement("p");
      if (block.type === "h") {
        const strong = doc.createElement("strong");
        appendLines(doc, strong, block.lines);
        paragraph.append(strong);
      } else appendLines(doc, paragraph, block.lines);
      fragment.append(paragraph);
    }
  }
  return fragment;
}
