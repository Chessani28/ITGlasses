"""Clean sentences from validated manual evidence, for answers made only of the manual's own words.

Deterministic: drops cover and index pages, lone page numbers, the "Review History" block, numbered all-caps
headings and bullet marks; joins lines a PDF wrapped mid-sentence; splits into sentences that keep their page.
"""
import re

from backend.app.assistant.procedures import is_cover_page, is_index_page

MAX_SENTENCES = 40
MAX_SENTENCE_CHARS = 400

_PAGE_ONLY = re.compile(r"^\d{1,3}$")
_REVIEW = re.compile(r"^(?:review|history|version description|revision|date reviewer|reviewer|date"
                     r"|\d+(?:\.\d+)+\s+initial version\b.*)$", re.IGNORECASE)
_HEADING = re.compile(r"^\d+(?:\.\d+)*\.?\s+[A-Z0-9][A-Z0-9 &'’()/,.\-]*$")
_BULLET = re.compile(r"^[➢•▪◦‣✓\-*]\s*")
_MARKER = re.compile(r"^(?:\d{1,2}|[a-z])[.)]\s+")
_ENDS = re.compile(r"""[.!?:;"”]$""")
_SPLIT = re.compile(r"""(?<=[.!?])\s+(?=[A-Z¿¡"“(])""")


def _units(text):
    """Logical lines of one chunk: cleaned, with PDF-wrapped continuations joined to the line above."""
    units = []
    for raw in (text or "").splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line or _PAGE_ONLY.match(line) or _REVIEW.match(line) or _HEADING.match(line):
            continue
        bullet = bool(_BULLET.match(line))
        line = _MARKER.sub("", _BULLET.sub("", line)).strip()
        if not line:
            continue
        continues = units and not bullet and line[0].islower() and not _ENDS.search(units[-1])
        if continues:
            units[-1] = f"{units[-1]} {line}"
        else:
            units.append(line)
    return units


def sentences(rows):
    """[{"id", "text", "page"}] in document order, at most MAX_SENTENCES of at most MAX_SENTENCE_CHARS each."""
    content = [row for row in rows
               if not is_index_page(row.get("text")) and not is_cover_page(row.get("text"), row.get("page"))]
    items = []
    for row in content or rows:
        for unit in _units(row.get("text")):
            for sentence in _SPLIT.split(unit):
                sentence = sentence.strip()
                if not sentence:
                    continue
                if len(sentence) > MAX_SENTENCE_CHARS:
                    sentence = sentence[:MAX_SENTENCE_CHARS].rsplit(" ", 1)[0]
                items.append({"id": len(items), "text": sentence, "page": row.get("page")})
                if len(items) == MAX_SENTENCES:
                    return items
    return items


def clean_excerpt(rows):
    """The same sentences as one readable text: the fallback when no sentence could be selected."""
    return " ".join(item["text"] for item in sentences(rows))
