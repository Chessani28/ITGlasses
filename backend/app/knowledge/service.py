import asyncio
from dataclasses import asdict
import hashlib
import inspect
import json
import logging
from pathlib import Path
import re
import sys
import time
import uuid

from .extract import validate, chunks
from .models import KnowledgeAnswer, KnowledgeError, Metadata
from .retrieval import Retriever, exact_ids, intent, tokens
from .social import detect_social_intent, social_response
from .store import Store, normalize


def reserve_answer(evidence, reason):
    """Answer when no grounded generation is available: the selected evidence, without table-of-contents or cover
    pages (unless every row is one). Only this text changes; the selection, ranking and sources do not."""
    from backend.app.assistant.procedures import is_cover_page, is_index_page

    index_pages = [row for row in evidence if is_index_page(row.get("text"))]
    covers = [row for row in evidence if row not in index_pages and is_cover_page(row.get("text"), row.get("page"))]
    rows = [row for row in evidence if row not in index_pages and row not in covers]
    if not rows:  # never an empty answer
        rows, index_pages, covers = list(evidence), [], []
    logging.getLogger("knowledge").info(
        "Knowledge reserve answer reason=%s rows=%d index_pages_dropped=%d cover_pages_dropped=%d",
        re.sub(r"[^A-Za-z0-9_]", "", str(reason))[:64], len(rows), len(index_pages), len(covers),
    )
    return "Según la documentación autorizada:\n\n" + "\n\n".join(row["text"] for row in rows)


# What a company question asked about, for the refusal without evidence: (this, a) in Spanish.
CHUNK_CHARS_DEFAULT = 900


def chunk_chars():
    """Largest chunk (characters) when indexing a manual. ITGLASSES_CHUNK_CHARS overrides it (80-4000); a changed
    value applies to documents indexed or reindexed afterwards."""
    import os
    raw = os.environ.get("ITGLASSES_CHUNK_CHARS", "").strip()
    return int(raw) if raw.isdigit() and 80 <= int(raw) <= 4000 else CHUNK_CHARS_DEFAULT


def embeddings_keep_min_bytes():
    """Available RAM needed to keep the embedding model loaded after a search: Whisper's 3 GiB plus a small margin: the voice service frees it anyway before giving up on Whisper.
    ITGLASSES_EMBEDDINGS_KEEP_MIN_MB overrides the default 3584 MB."""
    import os
    raw = os.environ.get("ITGLASSES_EMBEDDINGS_KEEP_MIN_MB", "").strip()
    megabytes = int(raw) if raw.isdigit() else 3584
    return megabytes * 1024 * 1024


# Best chunks of the chosen document given to the evidence selector (2 was too few: a wrong order meant "no evidence").
EVIDENCE_PIECES = 4

_ASKED_ABOUT = (
    (re.compile(r"\bpol[ií]ticas?\b", re.I), ("esa política", "una política")),
    (re.compile(r"\b(?:procedimientos?|pasos?|instrucciones|protocolos?)\b", re.I),
     ("ese procedimiento", "un procedimiento")),
)


# Words of a manual's title that name no manual in particular ("¿…según el manual?").
GENERIC_TITLE_WORDS = {"manual", "manuales", "guia", "guide", "politica", "policy", "documento", "document",
                       "procedimiento", "procedure", "instructivo", "de", "del", "y", "and"}


def closest_sentences(question, alternates, candidates, limit=2):
    """Up to `limit` candidate sentences (in their order) sharing most words with the question or its translations;
    a sentence needs 2 shared words, or the question's code ("PRN-3-A"). [] when none is close."""
    from .retrieval import code_hits, code_tokens
    asked = set(tokens(" ".join([question, *alternates])))
    codes = code_tokens(question)
    scored = []
    for index, item in enumerate(candidates):
        shared = len(asked & set(tokens(item["text"]))) + 3 * code_hits(codes, item["text"])
        if shared >= 2:
            scored.append((shared, index))
    best = sorted(scored, key=lambda pair: (-pair[0], pair[1]))[:limit]
    return [candidates[index] for _, index in sorted(best, key=lambda pair: pair[1])]


def evidence_rows(ranked, document_id, limit):
    """The best chunks of the chosen document for the evidence selector, without cover and index pages (they matched
    the title words and pushed the real answer out of the four), unless the document has nothing else."""
    from backend.app.assistant.procedures import is_cover_page, is_index_page
    own = [row for row in ranked if row["document_id"] == document_id]
    content = [row for row in own
               if not is_cover_page(row.get("text"), row.get("page")) and not is_index_page(row.get("text"))]
    return (content or own)[:limit]


def unverified_message(question):
    """The refusal when the manuals hold no evidence, with the word the question used: política, procedimiento, or
    the neutral "información" (manual, guía, empresa, cómo...)."""
    this, a = next((words for pattern, words in _ASKED_ABOUT if pattern.search(question or "")),
                   ("esa información", "información"))
    return (f"No pude verificar {this} en los manuales y documentos autorizados disponibles. "
            f"No puedo afirmar {a} de la empresa sin esa evidencia.")


class KnowledgeService:
    def __init__(self, root, embeddings, selector):
        self.root = Path(root)
        self.store = Store(self.root / "storage/knowledge")
        self.embeddings, self.selector = embeddings, selector
        self.retriever = Retriever(self.store, embeddings)
        self.lock = asyncio.Lock()
        self.sessions = {}

    async def ingest(self, filename, raw, metadata, document_id=None, expected_revision=None):
        extension = Path(filename).suffix.lower()
        validate(raw, extension)
        async with self.lock:
            old = self.store.get(document_id) if document_id else None
            if document_id and (old is None or old["withdrawn"]):
                raise KnowledgeError("not_found", "Document not found or withdrawn.", 404)
            if expected_revision is not None:
                if old["current_revision"] != expected_revision:
                    raise KnowledgeError("revision_changed", "Document changed during reindexing. Retry with the current revision.", 409)
                metadata = metadata.model_copy(update={"authorized": bool(old["authorized"])})
            document_id = document_id or str(uuid.uuid4())
            revision = old["current_revision"] + 1 if old else 1
            folder = self.root / "documents/managed" / document_id / f"{revision}-{uuid.uuid4().hex}"
            folder.mkdir(parents=True)
            path = folder / ("source" + extension)
            path.write_bytes(raw)
            # Isolate parsers from the API process; never execute document contents.
            process = await asyncio.create_subprocess_exec(sys.executable, "-m", "backend.app.knowledge.extract", str(path),
                cwd=Path(__file__).resolve().parents[3], stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            try:
                stdout, _ = await asyncio.wait_for(process.communicate(), timeout=30)
            except TimeoutError:
                process.kill()
                await process.wait()
                raise KnowledgeError("extraction_timeout", "Document extraction exceeded 30 seconds.", 422) from None
            try:
                result = json.loads(stdout)
            except (ValueError, UnicodeError):
                raise KnowledgeError("extraction_error", "Document extraction failed.", 422) from None
            if "error" in result:
                raise KnowledgeError(result["error"], result["message"], 422)
            if metadata.procedure_id is None:
                discovered = exact_ids("\n".join(b["text"] for b in result["blocks"]))
                # One "Procedimiento 024" names the document; several ("Procedimiento 1: …", "Procedimiento 2: …") are
                # the sections of a manual: indexed without an automatic id (it was refused, 2026-11-26).
                if len(discovered) == 1:
                    metadata = metadata.model_copy(update={"procedure_id": discovered[0]})
            # Secrets in the manual (tokens, keys, passwords) are never stored, embedded or answered.
            from .redaction import redact
            pieces = [{**piece, "text": redact(piece["text"])[0]}
                      for piece in chunks(result["blocks"], chunk_chars())]
            if len(pieces) > 1000:
                raise KnowledgeError("chunk_limit", "Documents are limited to 1000 chunks.", 413)
            fingerprint = await self.embeddings.fingerprint()
            try:
                vectors = await self.embeddings.embed([metadata.title + "\n" + metadata.scope + "\n" + c["section"][:150] + "\n" + c["text"] for c in pieces])
            finally:
                await self.embeddings.unload()
            self.store.commit_document(document_id, metadata, revision, Path(filename).name, path, raw,
                pieces, vectors, self.embeddings.model, fingerprint)
            return {**self.store.get(document_id), "chunk_count": len(pieces)}

    async def reindex(self, document_id):
        old = self.store.get(document_id)
        if not old or old["withdrawn"]:
            raise KnowledgeError("not_found", "Document not found or withdrawn.", 404)
        revision = self.store.revision(document_id)
        metadata = Metadata(**{key: old[key] for key in ["title", "scope", "procedure_id", "procedure_key", "authorized", "version"]})
        return await self.ingest(revision["filename"], Path(revision["path"]).read_bytes(), metadata, document_id, expected_revision=old["current_revision"])

    def procedure_catalog(self, document_id):
        """Return the ordered procedures/steps of an authorized document.

        Only documents that are authorized and not withdrawn are read, so the
        assistant can never surface steps from a restricted revision.
        """
        document = self.store.get(document_id)
        if not document or not document["authorized"] or document["withdrawn"]:
            return None
        chunks = self.store.document_chunks(document_id)
        if not chunks:
            return None
        from backend.app.assistant.procedures import parse_document
        procedures = parse_document(chunks)
        return {"document": document, "procedures": procedures}

    async def access(self, document_id, authorized=None, withdrawn=None):
        async with self.lock:
            if not self.store.get(document_id):
                raise KnowledgeError("not_found", "Document not found.", 404)
            self.store.access(document_id, authorized=authorized, withdrawn=withdrawn)
            return self.store.get(document_id)

    def _session(self, identifier):
        now = time.monotonic()
        self.sessions = {key: value for key, value in self.sessions.items() if now - value["time"] < 1800}
        if identifier and identifier not in self.sessions:
            raise KnowledgeError("conversation_expired", "Conversation expired. Submit the question without a conversation ID.", 409)
        identifier = identifier or str(uuid.uuid4())
        if identifier not in self.sessions:
            if len(self.sessions) >= 100:
                self.sessions.pop(min(self.sessions, key=lambda key: self.sessions[key]["time"]))
            self.sessions[identifier] = {"time": now, "candidates": [], "revisions": {}, "turns": [], "intent": "identify", "last_topic": None, "last_document_id": None}
        self.sessions[identifier]["time"] = now
        return identifier, self.sessions[identifier]

    @staticmethod
    def option(document):
        return {key: document[key] for key in ["id", "title", "scope", "version", "procedure_id", "current_revision"]}

    @staticmethod
    def source(row):
        return {"citation_id": row["id"], "document_id": row["document_id"], "title": row["title"],
            "filename": row["filename"], "version": row["version"], "revision": row["revision"],
            "procedure_id": row["procedure_id"], "page": row["page"], "section": row["section"],
            "paragraph": row["paragraph"], "sha256": row["sha256"], "quote": row["text"],
            "url": "/api/v1/knowledge/sources/" + row["id"]}

    # A word, or the end of a sentence ("." of "2.20" is not one).
    def _ram_is_short(self):
        """True when keeping the embedding model loaded could leave Whisper without room (or RAM cannot be read)."""
        try:
            return self.available_memory() < embeddings_keep_min_bytes()
        except Exception:  # noqa: BLE001 - unknown means "free it", as before
            return True

    @staticmethod
    def available_memory():
        from backend.app.voice.persistent_worker import available_system_memory
        return available_system_memory()

    _TERM = re.compile(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9_]*|[.!?:;](?=\s|$)|[>\n]")

    def manual_terms(self):
        """Names and acronyms of the authorized manuals (folded): all-caps words ("CPU", "SUMMIT") and capitalized
        words in the middle of a sentence ("Dominos", "Haven"). A question that names one belongs to the manuals
        even when no generic IT word gives it away. Cached per set of current revisions."""
        documents = self.store.documents(eligible_only=True)
        key = tuple(sorted((d["id"], d["current_revision"]) for d in documents))
        cached = getattr(self, "_manual_terms_cache", None)
        if cached and cached[0] == key:
            return cached[1]
        terms = set()
        for row in self.store.eligible_chunks():
            sentence_start = True
            for match in self._TERM.finditer(row["title"] + ".\n" + (row["text"] or "")):
                word = match.group()
                if word in ".!?:;>\n":
                    sentence_start = True
                    continue
                if len(word) >= 2 and (word.isupper() or (word[0].isupper() and not sentence_start)):
                    terms.add(normalize(word))
                sentence_start = False
        self._manual_terms_cache = (key, terms)
        return terms

    async def _select(self, question, rows, question_en=None):
        """The evidence selection, given the English question when the selector takes it. An invalid answer from the
        model is retried once; a second one means "no evidence" (None) instead of an error that the assistant would
        report as "could not consult the documentation"."""
        select = self.selector.select
        try:
            takes_english = "question_en" in inspect.signature(select).parameters
        except (TypeError, ValueError):
            takes_english = False
        extra = {"question_en": question_en} if takes_english and question_en else {}
        for attempt in range(2):
            try:
                return await select(question, rows, **extra)
            except KnowledgeError as exc:
                if exc.code != "grounding_error":
                    raise
                logging.getLogger("knowledge").warning("evidence selection invalid attempt=%d", attempt + 1)
        return None

    async def query(self, request):
        async with self.lock:
            cid, session = self._session(request.conversation_id)

            # Social intent detection (lightweight, deterministic)
            social = detect_social_intent(request.message)
            if social:
                return KnowledgeAnswer(status="answered", answer=social_response(social),
                    conversation_id=cid, intent="information", options=[], sources=[],
                    documentation_sufficient=True, retrieval={})

            mode = intent(request.message)

            # Contextual follow-up: if message starts with "y si/eso/eso no funciona" etc.
            # and we have a previous topic, try to use session context
            followup_patterns = re.compile(
                r"^(?:y\s+(?:si|eso|qué|que)|¿?y\s+(?:si|eso)|eso\s+no\s+funciona|si\s+no\s+funciona|y\s+si\s+no|pasame|pásame|dame|mu[eé]strame|muestrame|los pasos|las instrucciones)",
                re.IGNORECASE,
            )
            if request.conversation_id and session.get("last_topic") and followup_patterns.match(request.message.strip()):
                # Inherit intent and try to search with last topic context
                mode = session.get("intent", mode)
                # Prepend last topic to improve retrieval
                request = request.model_copy(update={"message": f"{session['last_topic']} {request.message}"})

            if request.conversation_id and mode == "information" and re.match(r"^(?:el |la |ese |esa |version |versión )", request.message.casefold()):
                mode = session["intent"]
            session["intent"] = mode
            session["turns"] = (session["turns"] + [request.message[:500]])[-4:]

            def respond(status, answer, documents=(), sources=(), stats=None, excerpts=()):
                session["candidates"] = [d["id"] for d in documents]
                session["revisions"] = {d["id"]: d["current_revision"] for d in documents}
                if documents:
                    session["last_topic"] = documents[0].get("title", session.get("last_topic"))
                    session["last_document_id"] = documents[0]["id"]
                return KnowledgeAnswer(status=status, answer=answer, conversation_id=cid, intent=mode,
                    options=[self.option(d) for d in documents], sources=list(sources),
                    documentation_sufficient=status == "answered", retrieval=stats or {},
                    excerpts=list(excerpts))

            def is_company_specific(text):
                """Heuristic: does the query look like a company procedure question?"""
                keywords = ["procedimiento", "manual", "política", "politica", "empresa", "oficial",
                            "protocolo", "guía", "guia", "instrucciones", "pasos", "cómo", "como"]
                t = text.lower()
                return any(k in t for k in keywords)

            def missing():
                # Determine appropriate message based on query type
                msg = request.message
                if is_company_specific(msg):
                    return respond("insufficient_evidence", unverified_message(msg))
                else:
                    return respond("insufficient_evidence",
                        "No encontré información sobre ese tema en los manuales y documentos disponibles. "
                        "Puedes consultar el Chat general para obtener una explicación más amplia.")

            documents = self.store.documents(eligible_only=True)
            available = {d["id"]: d for d in documents}
            ids = exact_ids(request.message)
            exact = bool(ids)
            candidates = None
            if exact:
                candidates = [d for d in documents if d["procedure_id"] in ids]
                if not candidates:
                    return missing()
            elif request.choice_document_id:
                if (request.choice_document_id not in session["candidates"] or request.choice_document_id not in available
                        or available[request.choice_document_id]["current_revision"] != session["revisions"].get(request.choice_document_id)):
                    return missing()
                candidates = [available[request.choice_document_id]]
                exact = True
            elif request.conversation_id and session["candidates"]:
                candidates = [available[key] for key in session["candidates"] if key in available and available[key]["current_revision"] == session["revisions"].get(key)]
                if not candidates:
                    return missing()

            # Identity ambiguity is deterministic and precedes embeddings/LLM calls.
            if exact and len(candidates) > 1:
                groups = {d["procedure_key"] for d in candidates}
                if len(groups) == 1:
                    return respond("conflict", "Hay varias versiones o copias del mismo procedimiento. Confirma cuál está vigente; no combinaré sus instrucciones.", candidates)
                return respond("clarification_required", "Hay varios procedimientos con ese identificador. ¿A cuál te refieres? Indica título, ámbito o selecciona una opción.", candidates)

            search_start = time.perf_counter()
            # The manuals may be in English while the question is in Spanish: search with both. Never for exact
            # identifiers; a failed translation means searching with the original only.
            translate = getattr(self.selector, "translate_query", None)
            alternates = []
            if translate and not exact:
                english = await translate(request.message)
                if english:
                    alternates.append(english)
            # The embedding model (~0.9 GB of RAM) stays loaded while RAM is plentiful (reloading it cost 2-3 s per
            # manual question) and is unloaded when Whisper Small (3 GiB minimum) would not fit next to it. The voice
            # service also unloads it before giving up on Whisper.
            try:
                ranked = await self.retriever.search(request.message,
                                                     [d["id"] for d in candidates] if candidates is not None else None,
                                                     alternates=alternates)
            finally:
                if self._ram_is_short():
                    await self.embeddings.unload()
            retrieval_ms = round((time.perf_counter() - search_start) * 1000, 2)
            if not ranked:
                return missing()
            best_by_doc = {}
            for row in ranked:
                best_by_doc.setdefault(row["document_id"], row)
            # Conservative gates; calibrated on synthetic positives and hard negatives.
            # When running in BM25 lexical-only mode, the semantic score is 0.0;
            # use the hybrid (BM25) score and overlap count instead.
            first_doc = list(best_by_doc.values())[0] if best_by_doc else None
            if first_doc and first_doc.get("lexical_only"):
                relevant = [r for r in best_by_doc.values() if r["overlap"] >= 2 or r.get("code_hit")]
            else:
                # The middle band needs two of the USER'S words in the chunk: words shared only with the English
                # translation ("SSD", "disk", "Windows") let general questions into unrelated manual pages.
                # A code of the question found as such ("C6000") is evidence by itself.
                relevant = [r for r in best_by_doc.values() if r["semantic"] >= 0.55 or r.get("code_hit") or
                            (r["semantic"] >= 0.42 and r.get("overlap_original", r["overlap"]) >= 2)]
            if exact:
                relevant = list(best_by_doc.values())[:1]
            if not relevant:
                return missing()
            best = relevant[0]
            rivals = [r for r in relevant[1:] if
                      (r["overlap"] >= best["overlap"] if best.get("lexical_only")
                       else abs(r["semantic"] - best["semantic"]) < 0.055)]
            if not exact and len(relevant) > 1:
                # The manual the question names wins: "¿Cuánta RAM… para el training?" → "Training Setup Guideline",
                # not the laptops manual that also talks about RAM, tied or ranked first (2026-11-26). Generic
                # words of a title ("manual", "guía") name no manual in particular.
                asked = set(tokens(request.message))
                named = [r for r in relevant
                         if asked & (set(tokens(available[r["document_id"]]["title"])) - GENERIC_TITLE_WORDS)]
                if len(named) == 1:
                    best, rivals = named[0], []
            selected_document = available[best["document_id"]]
            conflicts = [d for d in documents if d["procedure_key"] == selected_document["procedure_key"]]
            if len(conflicts) > 1 and not request.choice_document_id:
                return respond("conflict", "Existen varias versiones o copias del procedimiento. Necesito que confirmes la versión aplicable; no mezclaré instrucciones.", conflicts)
            if rivals and not exact:
                # The question names the manuals: "¿Cuál corresponde a tu caso?" alone gave no choice (by voice above all).
                tied = [selected_document] + [available[r["document_id"]] for r in rivals]
                titles = [f"«{d['title']}»" for d in tied]
                listed = ", ".join(titles[:-1]) + " y " + titles[-1]
                return respond("clarification_required",
                               f"Lo encontré en {listed}. ¿De cuál quieres la respuesta?", tied)
            # Explicit selection still cannot resolve a missing requested fact by itself.
            rows = evidence_rows(ranked, selected_document["id"], EVIDENCE_PIECES)
            stats = {"retrieval_ms": retrieval_ms, "semantic_score": best["semantic"], "hybrid_score": best["hybrid"], "candidate_documents": len(best_by_doc)}
            if len(request.message) > 1800:
                return respond("clarification_required", "La consulta es extensa. Encontré este candidato; confirma si buscas su nombre o sus pasos y acota la pregunta para verificar la evidencia.", [selected_document], stats=stats)
            # A named structured procedure is exact source evidence, not an LLM decision.
            from backend.app.assistant.service import match_procedures
            catalog = self.procedure_catalog(selected_document["id"])
            matched = match_procedures(catalog["procedures"], request.message) if catalog and mode == "steps" else []
            if len(matched) == 1:
                citation_ids = {s["source"]["citation_id"] for s in matched[0]["steps"]}
                rows = [r for r in self.store.document_chunks(selected_document["id"]) if r["id"] in citation_ids]
                selected = list(range(len(rows)))
            elif exact and mode == "identify":
                selected = [0]
            else:
                start = time.perf_counter()
                selection_question = request.message
                if mode == "identify":
                    selection_question = "Identifica el procedimiento que coincide con esta descripción, sin dar pasos: " + request.message
                selected = await self._select(selection_question, rows, alternates[0] if alternates else None)
                stats["evidence_check_ms"] = round((time.perf_counter() - start) * 1000, 2)
                if selected is None:
                    return missing()
            if not selected:
                return missing()
            # Reauthorize each citation after the asynchronous model operation.
            evidence = []
            for index in selected:
                current = self.store.evidence(rows[index]["id"])
                if current is None or current["sha256"] != rows[index]["sha256"]:
                    return missing()
                evidence.append(current)
            sources = [self.source(row) for row in evidence]
            if mode == "identify":
                # Internal ids (citations, documents) never reach the text: the sources carry them.
                answer = (f'Documento identificado: «{selected_document["title"]}» (versión {selected_document["version"]}).\n'
                          'Si necesitas sus pasos, pregunta «¿Cuáles son los pasos de…?».')
                # Identification exposes title/provenance only, never destructive steps.
                sources = [{**source, "quote": "", "quote_available_at_source": True} for source in sources]
            else:
                if mode == "steps" and not any(re.search(r"(?:^|\n)\s*(?:\d+[.)]|[-*])\s|paso\s+\d", row["text"], re.I) for row in evidence):
                    return missing()
                if mode == "information":
                    # Only the manual's own sentences, picked by number (never written by the model); without a
                    # pick, the same evidence as clean text instead of the raw chunks.
                    from .excerpts import clean_excerpt, sentences as manual_sentences
                    title = selected_document["title"]
                    candidates = manual_sentences(evidence)
                    picker = getattr(self.selector, "pick_sentences", None)
                    picked = await picker(request.message, candidates) if picker and candidates else None
                    if picked:
                        excerpts = [{"text": candidates[i]["text"], "page": candidates[i]["page"]} for i in picked]
                        answer = f"Según «{title}»:\n\n" + " ".join(item["text"] for item in excerpts)
                        return respond("answered", answer, [selected_document], sources, stats, excerpts)
                    # No pick: the sentences that share most words with the question (and its English version),
                    # not the start of the evidence (it showed "Network Printer Troubleshooting Guide · Document ID…").
                    closest = closest_sentences(request.message, alternates, candidates)
                    if closest:
                        excerpts = [{"text": item["text"], "page": item["page"]} for item in closest]
                        answer = f"Según «{title}»:\n\n" + " ".join(item["text"] for item in excerpts)
                        # Marked: closest by words, not chosen as the answer (a follow-up does not take them).
                        return respond("answered", answer, [selected_document], sources,
                                       {**stats, "sentences": "closest"}, excerpts)
                    answer = f"Según «{title}»:\n\n" + (clean_excerpt(evidence) or reserve_answer(evidence, "empty"))
                    return respond("answered", answer, [selected_document], sources, stats)
                # The selected evidence itself: the model never writes what a manual says.
                answer = reserve_answer(evidence, "steps")
            return respond("answered", answer, [selected_document], sources, stats)
