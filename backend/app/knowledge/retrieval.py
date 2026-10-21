import re
import numpy as np
from .store import normalize, procedure_id
from .models import KnowledgeError

STOP = set("de del la las los el un una unos unas que cual cuales como se es son en para por con al lo le les y o a mi me tengo necesito pero no recuerdo ese eso donde quiero empresa proceso procedimiento pasos hacer please the what is it to for of my how".split())


def tokens(text):
    return list(dict.fromkeys(t for t in re.findall(r"[\w.-]+", normalize(text)) if len(t) > 1 and t not in STOP))


def exact_ids(text):
    return list(dict.fromkeys(procedure_id(m) for m in re.findall(
        r"\b(?:procedimiento|procedure|proc|id)\s*[#:]?\s*(?:numero\s+)?([a-z]*[-.]?\d+[\w.-]*)\b", normalize(text))))


def intent(text):
    text = normalize(text)
    if re.search(r"como se llama|nombre|que (?:es|significa)|definicion|no recuerdo", text):
        return "identify"
    if re.search(r"pasos|paso a paso|instrucciones|como (?:hago|hacer|ejecut|realiz)|detall|how (?:do|to)", text):
        return "steps"
    ids = exact_ids(text)
    if ids and {procedure_id(t) for t in tokens(text) if t != "numero"} <= set(ids):
        return "identify"
    return "information"


class Retriever:
    def __init__(self, store, embeddings):
        self.store, self.embeddings = store, embeddings

    async def search(self, question, candidates=None):
        rows = self.store.eligible_chunks()
        if candidates is not None:
            rows = [r for r in rows if r["document_id"] in candidates]
        if not rows:
            return []
        fingerprint = await self.embeddings.fingerprint()
        if any(r["embedding_digest"] != fingerprint or r["embedding_model"] != self.embeddings.model for r in rows):
            raise KnowledgeError("reindex_required", "The embedding model changed. Reindex the eligible documents before querying.", 409)
        # All query segments contribute; no silent truncation of long questions.
        segments = [question[i:i + 1000] for i in range(0, len(question), 1000)]
        query_vectors = await self.embeddings.embed(segments, query=True)
        vectors = np.asarray([np.frombuffer(r["vector"], dtype="<f4") for r in rows])
        if vectors.ndim != 2 or vectors.shape[1] != query_vectors.shape[1]:
            raise KnowledgeError("reindex_required", "Embedding dimensions differ. Reindex documents.", 409)
        scores = (vectors @ query_vectors.T).max(axis=1)
        lexical = self.store.lexical(tokens(question))
        semantic_order = sorted(range(len(rows)), key=lambda i: (-float(scores[i]), rows[i]["id"]))
        ranks = {i: rank + 1 for rank, i in enumerate(semantic_order)}
        query_tokens = set(tokens(question))
        result = []
        for index, row in enumerate(rows):
            overlap = len(query_tokens & set(tokens(row["title"] + " " + row["scope"] + " " + row["text"])))
            lexical_rank = lexical.get(row["id"])
            score = 1 / (60 + ranks[index]) + (1 / (60 + lexical_rank) if lexical_rank else 0)
            result.append({**row, "semantic": round(float(scores[index]), 6), "hybrid": score, "overlap": overlap})
        return sorted(result, key=lambda r: (-r["hybrid"], -r["semantic"], r["id"]))
