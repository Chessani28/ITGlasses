"""Voz 2: Whisper vocabulary from the authorized knowledge base (never returned to clients)."""
import sqlite3

from backend.app.voice.vocabulary import (FIXED_TERMS, MAX_CHARS, MAX_TERMS, build, clean_term,
                                          from_database_readonly, from_knowledge, signature)


def test_priority_order_and_hint_format():
    vocabulary = build(["Training Setup Guideline", "Guía de Instalación"], ["COMPUTER PREPARATION"])
    kinds = [t.kind for t in vocabulary.terms]
    assert kinds[: len(FIXED_TERMS)] == ["fixed"] * len(FIXED_TERMS)  # acronyms are selected first
    assert vocabulary.hint.startswith("Training Setup Guideline, Guía de Instalación, COMPUTER PREPARATION, BIOS")
    assert [t.text for t in vocabulary.candidates] == ["Training Setup Guideline", "Guía de Instalación",
                                                       "COMPUTER PREPARATION"]


def test_duplicates_files_and_unsayable_terms_are_left_out():
    vocabulary = build(["Training Setup Guideline", "training setup guideline", "FIEL 13-AGO-2026 Acuse_renovacion (18)",
                        "manual.pdf", "12345", "  ", None, "x" * 61],
                       ["Guía de Instalación", "GUIA DE INSTALACION", ""])
    texts = [t.text for t in vocabulary.terms if t.kind != "fixed"]
    assert texts == ["Training Setup Guideline", "Guía de Instalación"]
    assert clean_term("Reporte (2)") is None and clean_term("  Paso   uno ") == "Paso uno"


def test_limits_drop_headings_first_then_titles_never_acronyms():
    titles = [f"Documento número {i}" for i in range(40)]
    headings = [f"HEADING NUMBER {i}" for i in range(40)]
    vocabulary = build(titles, headings)
    stats = vocabulary.stats()
    assert stats["fixed"] == len(FIXED_TERMS)
    assert stats["terms"] <= MAX_TERMS and stats["chars"] <= MAX_CHARS
    # Titles win over headings: every title is placed before any heading; a short heading may still fill the
    # space left over when the next title no longer fits.
    kinds = [t.kind for t in vocabulary.terms if t.kind in ("title", "heading")]
    assert stats["title"] > stats["heading"] and kinds == sorted(kinds, key=lambda kind: kind != "title")
    small = build(["Uno dos tres"], [], max_terms=len(FIXED_TERMS))
    assert [t.kind for t in small.terms] == ["fixed"] * len(FIXED_TERMS)


def test_stats_are_counts_only():
    stats = build(["Training Setup Guideline"], ["COMPUTER PREPARATION"]).stats()
    assert set(stats) == {"terms", "chars", "title", "heading", "fixed"}
    assert all(isinstance(value, int) for value in stats.values())


def test_signature_changes_with_revision_or_access():
    base = [{"id": "a", "current_revision": 1, "authorized": 1, "withdrawn": 0}]
    assert signature(base) == signature([dict(base[0])])
    assert signature(base) != signature([{**base[0], "current_revision": 2}])
    assert signature(base) != signature([{**base[0], "withdrawn": 1}])


class _Store:
    def documents(self, eligible_only=False):
        assert eligible_only, "only authorized, non-withdrawn documents"
        return [{"id": "d1", "title": "Training Setup Guideline"}, {"id": "d2", "title": "Guía de Instalación"}]


class _Knowledge:
    store = _Store()

    def procedure_catalog(self, document_id):
        return {"procedures": [{"title": "COMPUTER PREPARATION"}, {"title": ""}]} if document_id == "d1" else None


def test_from_knowledge_uses_eligible_documents_and_their_headings():
    vocabulary = from_knowledge(_Knowledge())
    assert [t.text for t in vocabulary.candidates] == ["Training Setup Guideline", "Guía de Instalación",
                                                       "COMPUTER PREPARATION"]


def test_readonly_database_skips_unauthorized_and_withdrawn(tmp_path):
    path = tmp_path / "knowledge.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE documents(id TEXT, title TEXT, scope TEXT, procedure_id TEXT, procedure_key TEXT,
                authorized INT, withdrawn INT, current_revision INT);
            CREATE TABLE revisions(document_id TEXT, revision INT, version TEXT, sha256 TEXT, filename TEXT);
            CREATE TABLE chunks(id TEXT, document_id TEXT, revision INT, text TEXT, page INT, paragraph INT,
                offset INT, citation_id TEXT, section TEXT);
            INSERT INTO documents VALUES ('a','Training Setup Guideline','s',NULL,NULL,1,0,1),
                ('b','Borrador interno','s',NULL,NULL,0,0,1), ('c','Manual retirado','s',NULL,NULL,1,1,1);
            INSERT INTO revisions VALUES ('a',1,'1','x','a.pdf'),('b',1,'1','x','b.pdf'),('c',1,'1','x','c.pdf');
        """)
    before = path.read_bytes()
    titles = [t.text for t in from_database_readonly(path).terms if t.kind == "title"]
    assert titles == ["Training Setup Guideline"]
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["knowledge.sqlite3"]  # no -wal/-shm side files
