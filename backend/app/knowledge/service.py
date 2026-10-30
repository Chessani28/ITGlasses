import asyncio
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import uuid

from .extract import validate, chunks
from .models import KnowledgeAnswer, KnowledgeError, Metadata
from .retrieval import Retriever, exact_ids, intent
from .social import detect_social_intent, social_response
from .store import Store


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
                if len(discovered) > 1:
                    raise KnowledgeError("procedure_metadata_required", "Multiple procedure identifiers were found. Supply the primary procedure ID explicitly or split the document.", 422)
                if discovered:
                    metadata = metadata.model_copy(update={"procedure_id": discovered[0]})
            pieces = chunks(result["blocks"])
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

            def respond(status, answer, documents=(), sources=(), stats=None):
                session["candidates"] = [d["id"] for d in documents]
                session["revisions"] = {d["id"]: d["current_revision"] for d in documents}
                if documents:
                    session["last_topic"] = documents[0].get("title", session.get("last_topic"))
                    session["last_document_id"] = documents[0]["id"]
                return KnowledgeAnswer(status=status, answer=answer, conversation_id=cid, intent=mode,
                    options=[self.option(d) for d in documents], sources=list(sources),
                    documentation_sufficient=status == "answered", retrieval=stats or {})

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
                    return respond("insufficient_evidence",
                        "No pude verificar ese procedimiento en los manuales y documentos autorizados disponibles. "
                        "No puedo afirmar un procedimiento de la empresa sin esa evidencia.")
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
            try:
                ranked = await self.retriever.search(request.message, [d["id"] for d in candidates] if candidates is not None else None)
            finally:
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
                relevant = [r for r in best_by_doc.values() if r["overlap"] >= 2]
            else:
                relevant = [r for r in best_by_doc.values() if r["semantic"] >= 0.55 or (r["semantic"] >= 0.42 and r["overlap"] >= 2)]
            if exact:
                relevant = list(best_by_doc.values())[:1]
            if not relevant:
                return missing()
            best = relevant[0]
            rivals = [r for r in relevant[1:] if
                      (r["overlap"] >= best["overlap"] if best.get("lexical_only")
                       else abs(r["semantic"] - best["semantic"]) < 0.055)]
            selected_document = available[best["document_id"]]
            conflicts = [d for d in documents if d["procedure_key"] == selected_document["procedure_key"]]
            if len(conflicts) > 1 and not request.choice_document_id:
                return respond("conflict", "Existen varias versiones o copias del procedimiento. Necesito que confirmes la versión aplicable; no mezclaré instrucciones.", conflicts)
            if rivals and not exact:
                return respond("clarification_required", "Encontré varias coincidencias plausibles. ¿Cuál corresponde a tu caso?", [selected_document] + [available[r["document_id"]] for r in rivals])
            # Explicit selection still cannot resolve a missing requested fact by itself.
            rows = [r for r in ranked if r["document_id"] == selected_document["id"]][:2]
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
                selected = await self.selector.select(selection_question, rows)
                stats["evidence_check_ms"] = round((time.perf_counter() - start) * 1000, 2)
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
                answer = f'Documento identificado: «{selected_document["title"]}» (versión {selected_document["version"]}). [{evidence[0]["id"]}]\nNo se incluyen pasos de ejecución porque la consulta pide identificar el procedimiento.'
                # Identification exposes title/provenance only, never destructive steps.
                sources = [{**source, "quote": "", "quote_available_at_source": True} for source in sources]
            else:
                if mode == "steps" and not any(re.search(r"(?:^|\n)\s*(?:\d+[.)]|[-*])\s|paso\s+\d", row["text"], re.I) for row in evidence):
                    return missing()
                # Try grounded generation with Ollama first
                try:
                    if hasattr(self.selector, "generate_grounded_answer"):
                        answer = await self.selector.generate_grounded_answer(request.message, evidence)
                    else:
                        answer = "Según la documentación autorizada:\n\n" + "\n\n".join(row["text"] for row in evidence)
                except Exception:
                    answer = "Según la documentación autorizada:\n\n" + "\n\n".join(row["text"] for row in evidence)
            return respond("answered", answer, [selected_document], sources, stats)
