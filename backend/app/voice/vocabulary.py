"""Speech vocabulary for Whisper (Voz 2): hint terms built from the authorized knowledge base.

Titles of authorized, non-withdrawn documents, their procedure headings and fixed IT acronyms, deduplicated and
capped. The vocabulary only travels from the backend to the Whisper worker: it is never returned to clients and
never logged (only its counts). Standard library only, so the offline evaluation can import it from the Whisper
environment.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import closing
import unicodedata
from dataclasses import dataclass
from pathlib import Path

MAX_TERMS = 60
MAX_CHARS = 600
MAX_TERM_CHARS = 60
SEPARATOR = ", "
FIXED_TERMS = (
    "BIOS", "SSD", "NVMe", "VPN", "DNS", "DHCP", "IP", "RAM", "USB", "CPU", "GPU", "Wi-Fi",
    "Outlook", "Windows", "Active Directory",
)
# File names, upload copies ("(18)"), underscores: not something a person says.
_FILE_LIKE = re.compile(r"_|\.(?:pdf|docx?|txt|md|xlsx?|pptx?|csv)\s*$|\(\d+\)\s*$", re.IGNORECASE)


def fold(text: str) -> str:
    """Case- and accent-insensitive key: 'Guía de Instalación' == 'guia de instalacion'."""
    text = unicodedata.normalize("NFKD", text.casefold())
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


def clean_term(value) -> str | None:
    """A sayable term, or None: not text, empty, longer than 60 characters, file-like or without letters."""
    if not isinstance(value, str):
        return None
    term = " ".join(value.split())
    if not term or len(term) > MAX_TERM_CHARS or _FILE_LIKE.search(term):
        return None
    if not any(c.isalpha() for c in term):
        return None
    return term


@dataclass(frozen=True)
class Term:
    text: str
    kind: str  # "title", "heading", "fixed" or "alias" (Spanish name of an English heading)

    @property
    def words(self) -> int:
        return len(self.text.split())


@dataclass(frozen=True)
class Vocabulary:
    terms: tuple[Term, ...] = ()

    @property
    def hint(self) -> str:
        """Text for Whisper's hotwords / initial_prompt: titles, headings, acronyms, then the Spanish aliases (last,
        so a token limit cuts them first)."""
        order = {"title": 0, "heading": 1, "fixed": 2, "alias": 3}
        return SEPARATOR.join(t.text for t in sorted(self.terms, key=lambda t: order[t.kind]))

    @property
    def candidates(self) -> tuple[Term, ...]:
        """Correction targets: titles, headings and their aliases of two words or more (never single acronyms)."""
        return tuple(t for t in self.terms if t.kind in ("title", "heading", "alias") and t.words >= 2)

    @property
    def aliases(self) -> int:
        """Number of Spanish heading aliases (a count: safe to log)."""
        return sum(t.kind == "alias" for t in self.terms)

    def stats(self) -> dict:
        """Counts only: safe to log."""
        kinds = {kind: sum(t.kind == kind for t in self.terms) for kind in ("title", "heading", "fixed")}
        return {"terms": len(self.terms), "chars": len(self.hint), **kinds}


def build(titles, headings, fixed=FIXED_TERMS, max_terms=MAX_TERMS, max_chars=MAX_CHARS, aliases=()) -> Vocabulary:
    """Priority when the limits are reached: acronyms always stay, then titles, then headings, then aliases."""
    selected: list[Term] = []
    seen: set[str] = set()
    used = 0
    for kind, values in (("fixed", fixed), ("title", titles), ("heading", headings), ("alias", aliases)):
        for value in values:
            term = clean_term(value)
            if term is None or fold(term) in seen:
                continue
            cost = len(term) + (len(SEPARATOR) if selected else 0)
            if len(selected) >= max_terms or used + cost > max_chars:
                continue  # A shorter later term may still fit.
            selected.append(Term(term, kind))
            seen.add(fold(term))
            used += cost
    return Vocabulary(tuple(selected))


def signature(documents) -> tuple:
    """Changes whenever an eligible document, its revision or its access changes: rebuild then, not per request."""
    return tuple(sorted((str(d.get("id")), d.get("current_revision"), d.get("authorized"), d.get("withdrawn"))
                        for d in documents))


def _headings(procedures) -> list[str]:
    return [p.get("title") for p in procedures or [] if isinstance(p, dict) and p.get("title")]


def _aliases(titles, headings, aliases, nicknames) -> list[str]:
    """Low-priority Spanish names: document nicknames first, then the heading aliases (the last cut first)."""
    from backend.app.assistant.named import alias_of, nicknames_of

    found = [nickname for title in titles for nickname in nicknames_of(title)] if nicknames else []
    if aliases:
        found.extend(alias for alias in (alias_of(heading) for heading in headings) if alias)
    return found


def from_knowledge(knowledge, aliases=False, nicknames=False) -> Vocabulary:
    """Backend: the KnowledgeService in use (authorized, non-withdrawn documents only). ``aliases`` adds the
    Spanish names of the English headings that exist, ``nicknames`` those of the documents."""
    documents = knowledge.store.documents(eligible_only=True)
    headings = []
    for document in documents:
        catalog = knowledge.procedure_catalog(document["id"]) or {}
        headings.extend(_headings(catalog.get("procedures")))
    titles = [d["title"] for d in documents]
    return build(titles, headings, aliases=_aliases(titles, headings, aliases, nicknames))


_ELIGIBLE = """SELECT d.id, d.title FROM documents d JOIN revisions r ON r.document_id=d.id
    AND r.revision=d.current_revision WHERE d.authorized=1 AND d.withdrawn=0"""
_CHUNKS = """SELECT c.*, d.title, d.scope, d.procedure_id, d.procedure_key, r.version, r.sha256, r.filename
    FROM chunks c JOIN documents d ON d.id=c.document_id AND d.current_revision=c.revision
    JOIN revisions r ON r.document_id=d.id AND r.revision=c.revision
    WHERE c.document_id=? AND d.authorized=1 AND d.withdrawn=0
    ORDER BY COALESCE(c.page,0), COALESCE(c.paragraph,0), c.offset"""


def from_database_readonly(path: Path, aliases=False, nicknames=False, max_chars=MAX_CHARS) -> Vocabulary:
    """Offline evaluation: the same SQL as Store, opened read-only and immutable (no locks, no side files)."""
    from backend.app.assistant.procedures import parse_document

    uri = Path(path).resolve().as_uri() + "?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.row_factory = sqlite3.Row
        documents = [dict(row) for row in db.execute(_ELIGIBLE)]
        headings = []
        for document in documents:
            chunks = [dict(row) for row in db.execute(_CHUNKS, (document["id"],))]
            headings.extend(_headings(parse_document(chunks)))
    titles = [d["title"] for d in documents]
    return build(titles, headings, max_chars=max_chars, aliases=_aliases(titles, headings, aliases, nicknames))
