"""Deterministic extraction of ordered procedure steps from authorized evidence.

The parser is intentionally free of model calls: numbered steps, section
headings and sub-steps are read straight from the document text so that the
assistant can cite the exact chunk, page and paragraph a step came from.
"""
import re

_HEADING = re.compile(r"^(\d+)\.\s+([A-Z0-9][A-Z0-9 &/,\-()'\.]{2,80})$")
_SUBHEADING = re.compile(r"^(\d+\.\d+)\s+(.+)$")
_VERSION_ROW = re.compile(r"^\d+\.\d+\s+(?:Initial\s+Version|Version)\b", re.IGNORECASE)
_STEP = re.compile(r"^(\d+)\.\s+(.+)$")
_SUBSTEP = re.compile(r"^([a-z])\.\s+(.+)$")
_PAGE_ONLY = re.compile(r"^\d{1,4}$")
_NOISE = re.compile(
    r"^(?:review|history|version|description|revision|date|reviewer|notes?|table of contents)\b",
    re.IGNORECASE,
)
_DOTTED_LEADER = re.compile(r"[.\u2024]{2,}.*$", re.DOTALL)
# Index / table-of-contents entries such as "1. COMPUTER PREPARATION ... Page 2".
_TOC_LINE = re.compile(r"(?:\.{3,}|\u2024{2,})")
_BULLET = re.compile(r"^[\u2022\u25aa\u27a2\u2192\u27a4\u2043>]\s*(.+)$")
_SECTION = re.compile(r"^(\d+\.\d+(?:\.\d+)?)\s+([A-Za-z].*)$")
# "Procedimiento 10: Reinicio de servidor" / "Procedure 3: ..." headings.
_NAMED_HEADING = re.compile(r"^(?:Procedimiento|Procedure)\s+(\d+)\s*[:\-\u2013]\s*(\S.{1,80})$", re.IGNORECASE)
# Steps written on one line: "Pasos: 1. Apagar el servidor. 2. Esperar 30 segundos. 3. Encender el servidor."
_INLINE_STEPS = re.compile(r"^(?:Pasos?|Steps?)\s*:\s*(.+)$", re.IGNORECASE)
_INLINE_NUMBER = re.compile(r"(?:^|(?<=\s))(\d{1,2})\.\s+")


def inline_steps(text):
    """[(1, "Apagar el servidor."), ...] when the text is a list numbered 1..n (n >= 2), otherwise []."""
    parts = _INLINE_NUMBER.split(text.strip())
    if len(parts) < 5 or parts[0].strip():
        return []
    numbers = [int(number) for number in parts[1::2]]
    texts = [value.strip() for value in parts[2::2]]
    if numbers != list(range(1, len(numbers) + 1)) or not all(texts):
        return []
    return list(zip(numbers, texts))


def clean_title(text: str) -> str:
    text = _DOTTED_LEADER.sub("", text).strip()
    text = re.sub(r"\s*\bPage\s+\d+(?:\s*-\s*\d+)?\s*$", "", text, flags=re.IGNORECASE).strip()
    return re.sub(r"\s+", " ", text)


def _is_noise(line: str) -> bool:
    return bool(_NOISE.match(line)) or bool(_VERSION_ROW.match(line))


def _is_toc(line: str) -> bool:
    """Index rows repeat section titles and are never real content."""
    return bool(_TOC_LINE.search(line)) or bool(re.search(r"\bPage\s+\d+\s*(?:-\s*\d+)?\s*$", line))


_INDEX_TITLE = re.compile(r"^(?:index|indice|índice|table of contents|contents|contenido)$", re.IGNORECASE)
INDEX_PAGE_SHARE = 0.6


def is_index_page(text) -> bool:
    """True for a table-of-contents page: most of its lines are index rows ("1. X ........ Page 2")."""
    lines = [line.strip() for line in (text or "").splitlines()]
    lines = [line for line in lines if line and not _PAGE_ONLY.match(line)]
    if not lines:
        return False
    index_rows = sum(1 for line in lines if _is_toc(line) or _INDEX_TITLE.match(line))
    return index_rows / len(lines) >= INDEX_PAGE_SHARE


def parse_document(chunks) -> list:
    """Return ordered procedures with ordered steps for a list of chunks.

    Each chunk is a mapping with at least ``text``; ``page``, ``paragraph``,
    ``citation_id`` and ``url`` are copied into every step it contributes.
    """
    procedures = []
    current = None
    section = ""

    def new_procedure(number, title):
        nonlocal current, section
        current = {"number": number, "title": title, "steps": [], "notes": []}
        section = ""
        procedures.append(current)
        return current

    for chunk in chunks:
        citation = {
            "page": chunk.get("page"),
            "section": chunk.get("section"),
            "paragraph": chunk.get("paragraph"),
            "citation_id": chunk.get("citation_id") or chunk.get("id"),
            "document_id": chunk.get("document_id"),
            "title": chunk.get("title"),
            "version": chunk.get("version"),
            "url": chunk.get("url"),
        }
        for raw_line in (chunk.get("text") or "").splitlines():
            line = raw_line.strip()
            if not line or _PAGE_ONLY.match(line) or _is_noise(line) or _is_toc(line):
                continue
            named_heading = _NAMED_HEADING.match(line)
            if named_heading:
                new_procedure(named_heading.group(1), clean_title(named_heading.group(2)))
                continue
            inline = _INLINE_STEPS.match(line)
            listed = inline_steps(inline.group(1)) if inline else []
            if listed:
                if current is None:
                    new_procedure("", "")
                for number, text in listed:
                    if not any(s["number"] == number and s["section"] == section for s in current["steps"]):
                        current["steps"].append({"number": number, "text": text, "substeps": [],
                                                 "section": section, "source": dict(citation)})
                continue
            subsection = _SECTION.match(line)
            if subsection:
                if current is None:
                    new_procedure(subsection.group(1), clean_title(subsection.group(2)))
                else:
                    section = clean_title(subsection.group(2))
                continue
            heading = _HEADING.match(line)
            if heading and not heading.group(2).endswith("."):
                new_procedure(heading.group(1), clean_title(heading.group(2)))
                continue
            step = _STEP.match(line)
            if step:
                if current is None:
                    new_procedure("", "")
                entry = {"number": int(step.group(1)), "text": step.group(2).strip(),
                         "substeps": [], "section": section, "source": dict(citation)}
                existing = next(
                    (s for s in current["steps"]
                     if s["number"] == entry["number"] and s["section"] == entry["section"]),
                    None,
                )
                if existing is None:
                    current["steps"].append(entry)
                elif len(entry["text"]) > len(existing["text"]):
                    existing["text"] = entry["text"]
                    existing["source"] = dict(citation)
                continue
            substep = _SUBSTEP.match(line)
            if substep and current:
                if current["steps"]:
                    current["steps"][-1]["substeps"].append(substep.group(2).strip())
                elif current["notes"]:
                    # First substep without prior steps: create step 1 from first note,
                    # then add this substep as the first substep of step 1
                    first_note = current["notes"].pop(0)
                    current["steps"].append({
                        "number": 1,
                        "text": first_note,
                        "substeps": [substep.group(2).strip()],
                        "section": section,
                        "source": dict(citation),
                    })
                else:
                    # No steps and no notes; create step 1 with this substep
                    current["steps"] = [{
                        "number": 1,
                        "text": substep.group(2).strip(),
                        "substeps": [],
                        "section": section,
                        "source": dict(citation),
                    }]
                continue
            bullet = _BULLET.match(line)
            if bullet:
                text = bullet.group(1).strip()
                if current["steps"]:
                    current["steps"][-1]["substeps"].append(text)
                elif current["notes"]:
                    # First bullet without prior steps: create step 1 from first note
                    first_note = current["notes"].pop(0)
                    current["steps"].append({
                        "number": 1,
                        "text": first_note,
                        "substeps": [],
                        "section": section,
                        "source": dict(citation),
                    })
                    # Add this bullet as the first substep of step 1
                    current["steps"][-1]["substeps"].append(text)
                elif current is not None:
                    current["notes"].append(text)
                continue
            if current and current["steps"]:
                last = current["steps"][-1]
                last["text"] = (last["text"] + " " + line).strip()
            elif current is not None:
                current["notes"].append(line)
    for procedure in procedures:
        procedure["steps"].sort(key=lambda s: (s["section"], s["number"]))
    return [p for p in procedures if p["steps"]]


def flatten(procedures):
    """Flatten procedures into a single ordered list of step references.

    The position of each entry in the returned list is its ordinal position,
    which is what "el cuarto paso" refers to.
    """
    flat = []
    for procedure in procedures:
        for step in procedure["steps"]:
            flat.append({**step, "procedure": procedure["title"],
                         "procedure_number": procedure["number"]})
    return flat
