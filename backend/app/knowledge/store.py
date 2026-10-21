from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import sqlite3
import unicodedata
import uuid


def normalize(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c)).strip()


def procedure_id(value: str | None) -> str | None:
    if not value:
        return None
    value = normalize(value)
    return str(int(value)) if value.isdecimal() else value


class Store:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "knowledge.sqlite3"
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS documents(
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, scope TEXT NOT NULL,
                    procedure_id TEXT, procedure_key TEXT NOT NULL,
                    authorized INTEGER NOT NULL, withdrawn INTEGER NOT NULL DEFAULT 0,
                    current_revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS revisions(
                    document_id TEXT NOT NULL, revision INTEGER NOT NULL, version TEXT NOT NULL,
                    sha256 TEXT NOT NULL, filename TEXT NOT NULL, path TEXT NOT NULL,
                    embedding_model TEXT NOT NULL, embedding_digest TEXT NOT NULL,
                    PRIMARY KEY(document_id,revision));
                CREATE TABLE IF NOT EXISTS chunks(
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    text TEXT NOT NULL, page INTEGER, section TEXT, paragraph INTEGER,
                    offset INTEGER, vector BLOB NOT NULL);
                CREATE INDEX IF NOT EXISTS chunk_document ON chunks(document_id,revision);
                CREATE VIRTUAL TABLE IF NOT EXISTS lexical USING fts5(chunk_id UNINDEXED, text,
                    tokenize='unicode61 remove_diacritics 2');
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def documents(self, eligible_only=False):
        condition = " WHERE d.authorized=1 AND d.withdrawn=0" if eligible_only else ""
        with self.connect() as db:
            return [dict(row) for row in db.execute('''SELECT d.*,r.version,r.sha256,r.filename,
                r.embedding_model,r.embedding_digest FROM documents d JOIN revisions r ON
                r.document_id=d.id AND r.revision=d.current_revision''' + condition)]

    def get(self, document_id):
        return next((d for d in self.documents() if d["id"] == document_id), None)

    def access(self, document_id, authorized=None, withdrawn=None):
        with self.connect() as db:
            if authorized is not None:
                db.execute("UPDATE documents SET authorized=? WHERE id=?", (int(authorized), document_id))
            if withdrawn is not None:
                db.execute("UPDATE documents SET withdrawn=? WHERE id=?", (int(withdrawn), document_id))

    def commit_document(self, document_id, metadata, revision, filename, path, raw, chunks, vectors, embedding_model, digest):
        identity = metadata.procedure_key or "|".join([normalize(metadata.scope), procedure_id(metadata.procedure_id) or "", normalize(metadata.title)])
        with self.connect() as db:
            db.execute('''INSERT INTO documents(id,title,scope,procedure_id,procedure_key,authorized,current_revision)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,
                scope=excluded.scope, procedure_id=excluded.procedure_id,
                procedure_key=excluded.procedure_key, authorized=excluded.authorized,
                current_revision=excluded.current_revision''',
                (document_id, metadata.title, metadata.scope, procedure_id(metadata.procedure_id), normalize(identity), int(metadata.authorized), revision))
            db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?)", (document_id, revision, metadata.version,
                hashlib.sha256(raw).hexdigest(), filename, str(path), embedding_model, digest))
            for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
                identifier = str(uuid.uuid5(uuid.UUID(document_id), f"{revision}:{index}"))
                db.execute("INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?)", (identifier, document_id, revision,
                    chunk["text"], chunk["page"], chunk["section"], chunk["paragraph"], chunk["offset"], vector.astype("<f4").tobytes()))
                db.execute("INSERT INTO lexical(chunk_id,text) VALUES(?,?)", (identifier, metadata.title + " " + metadata.scope + " " + chunk["section"] + " " + chunk["text"]))

    def revision(self, document_id):
        with self.connect() as db:
            row = db.execute('''SELECT r.* FROM revisions r JOIN documents d ON d.id=r.document_id
                AND d.current_revision=r.revision WHERE d.id=?''', (document_id,)).fetchone()
            return dict(row) if row else None

    def eligible_chunks(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('''SELECT c.*,d.title,d.scope,d.procedure_id,
                d.procedure_key,r.version,r.sha256,r.filename,r.embedding_model,r.embedding_digest
                FROM chunks c JOIN documents d ON c.document_id=d.id AND c.revision=d.current_revision
                JOIN revisions r ON r.document_id=d.id AND r.revision=c.revision
                WHERE d.authorized=1 AND d.withdrawn=0''')]

    def evidence(self, identifier):
        return next((c for c in self.eligible_chunks() if c["id"] == identifier), None)

    def lexical(self, tokens):
        expression = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens[:80])
        if not expression:
            return {}
        with self.connect() as db:
            rows = db.execute('''SELECT l.chunk_id,bm25(lexical) score FROM lexical l
                JOIN chunks c ON c.id=l.chunk_id JOIN documents d ON d.id=c.document_id
                WHERE lexical MATCH ? AND d.authorized=1 AND d.withdrawn=0
                AND c.revision=d.current_revision ORDER BY score LIMIT 200''', (expression,)).fetchall()
            return {row["chunk_id"]: index + 1 for index, row in enumerate(rows)}
