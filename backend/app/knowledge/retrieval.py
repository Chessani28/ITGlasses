import re
import numpy as np
from .store import normalize, procedure_id
from .models import KnowledgeError

STOP = set("de del la las los el un una unos unas que cual cuales como se es son en para por con al lo le les y o a mi me tengo necesito pero no recuerdo ese eso donde quiero empresa proceso procedimiento pasos hacer please the what is it to for of my how".split())
# English function words: with the English translation of a question they inflated the word overlap ("and", "do").
STOP |= set("an and are be do does did in on at by as or if which who whom where when why should would could can "
            "will was were this that these those with from you your we our they their he she its there here "
            "about into than then so".split())


# A code of a manual: letters and digits in one word ("C6000", "J0511", "PRN-3-A"); never a quantity with its unit
# ("16GB", "65W") nor a plain number. The one word a question about an error code shares with the manual is the code.
_CODE = re.compile(r"(?<![\w-])[a-z0-9]+(?:-[a-z0-9]+)*(?![\w-])")
_UNIT = re.compile(r"\d+(?:[.,]\d+)?(?:gb|mb|tb|kb|ghz|mhz|hz|w|v|mm|cm|in|p|k|x|h|s|ms)")


def code_tokens(text):
    """The codes of a text (folded): words with letters and digits, at least 4 characters without hyphens."""
    found = set()
    for word in _CODE.findall(normalize(text or "")):
        bare = word.replace("-", "")
        if (len(bare) >= 4 and re.search(r"\d", bare) and re.search(r"[a-z]", bare)
                and not _UNIT.fullmatch(bare)):
            found.add(word)
    return found


def code_hits(codes, text):
    """How many of the question's codes appear as such in the text."""
    if not codes:
        return 0
    folded = normalize(text or "")
    return sum(1 for code in codes if re.search(r"(?<![\w-])" + re.escape(code) + r"(?![\w-])", folded))


def tokens(text):
    return list(dict.fromkeys(t for t in re.findall(r"[\w.-]+", normalize(text)) if len(t) > 1 and t not in STOP))


def exact_ids(text):
    return list(dict.fromkeys(procedure_id(m) for m in re.findall(
        r"\b(?:procedimiento|procedure|proc|id)\s*[#:]?\s*(?:numero\s+)?([a-z]*[-.]?\d+[\w.-]*)\b", normalize(text))))


def intent(text):
    text = normalize(text)
    # Steps take priority over identification when both are requested
    if re.search(r"pasos|paso a paso|instrucciones|como (?:hago|hacer|ejecut|realiz)|detall|how (?:do|to)|mu[eé]strame|pasame|dame los", text):
        return "steps"
    # "qué es lo primero / lo que…" asks for information, not for a name.
    # "que es" as whole words: "confirmar que esté corriendo" (folded "que este") is not a definition question.
    # "cómo se llama la carpeta / el botón" asks for a fact of the manual; only the process itself is a name question.
    # "¿Qué significa el error C6000?" asks what the manual says about the code: information, not a name.
    if code_tokens(text) and re.search(r"que (?:es|significa)|significado", text):
        return "information"
    # "nombre" is a name question only for the process or the document itself ("dame su nombre", "el nombre del
    # procedimiento"); "¿con qué nombre se restaura el archivo?" / "el nombre del equipo" ask a fact of the manual.
    if re.search(r"como se llama(?!\s+(?:la|las|los|el)\s+(?!proceso|procedimiento))"
                 r"|(?:su|tu) nombre|nombre (?:del|de la|de este|de esta) (?:proceso|procedimiento|manual|documento|"
                 r"guia|politica)"
                 r"|que (?:es\b(?! lo\b)|significa)|definicion|no recuerdo", text):
        return "identify"
    ids = exact_ids(text)
    if ids and {procedure_id(t) for t in tokens(text) if t != "numero"} <= set(ids):
        return "identify"
    return "information"


class Retriever:
    def __init__(self, store, embeddings):
        self.store, self.embeddings = store, embeddings

    async def search(self, question, candidates=None, alternates=()):
        """Chunks ranked for the question. Alternates (e.g. its English translation) count as more ways to say it:
        each chunk keeps its best semantic score over all of them, and their words join the lexical search."""
        alternates = [text for text in alternates if text and text.strip() and text.strip() != question.strip()]
        rows = self.store.eligible_chunks()
        if candidates is not None:
            rows = [r for r in rows if r["document_id"] in candidates]
        if not rows:
            return []
        try:
            fingerprint = await self.embeddings.fingerprint()
        except KnowledgeError as e:
            if e.code == "embedding_unavailable":
                # Fall back to lexical (BM25) search only when embedding model is unavailable
                pass
            else:
                raise
        else:
            if any(r["embedding_digest"] != fingerprint or r["embedding_model"] != self.embeddings.model for r in rows):
                raise KnowledgeError("reindex_required", "The embedding model changed. Reindex the eligible documents before querying.", 409)
            # All query segments contribute; no silent truncation of long questions.
            segments = [text[i:i + 1000] for text in [question, *alternates] for i in range(0, len(text), 1000)]
            query_vectors = await self.embeddings.embed(segments, query=True)
            vectors = np.asarray([np.frombuffer(r["vector"], dtype="<f4") for r in rows])
            if vectors.ndim != 2 or vectors.shape[1] != query_vectors.shape[1]:
                raise KnowledgeError("reindex_required", "Embedding dimensions differ. Reindex documents.", 409)
            scores = (vectors @ query_vectors.T).max(axis=1)
            words = tokens(" ".join([question, *alternates]))
            lexical = self.store.lexical(words)
            semantic_order = sorted(range(len(rows)), key=lambda i: (-float(scores[i]), rows[i]["id"]))
            ranks = {i: rank + 1 for rank, i in enumerate(semantic_order)}
            query_tokens = set(words)
            original_tokens = set(tokens(question))
            codes = code_tokens(question)
            result = []
            for index, row in enumerate(rows):
                chunk_tokens = set(tokens(row["title"] + " " + row["scope"] + " " + row["text"]))
                overlap = len(query_tokens & chunk_tokens)
                lexical_rank = lexical.get(row["id"])
                score = 1 / (60 + ranks[index]) + (1 / (60 + lexical_rank) if lexical_rank else 0)
                # overlap_original: only the user's own words (what the relevance gates were calibrated on).
                result.append({**row, "semantic": round(float(scores[index]), 6), "hybrid": score, "overlap": overlap,
                               "overlap_original": len(original_tokens & chunk_tokens),
                               "code_hit": code_hits(codes, row["text"])})
            # A piece with the question's code first: it is the evidence ("C6000" in the error code table).
            return sorted(result, key=lambda r: (-r["code_hit"], -r["hybrid"], -r["semantic"], r["id"]))
        # Fallback: lexical-only BM25 ranking without semantic scoring
        words = tokens(" ".join([question, *alternates]))
        lexical = self.store.lexical(words)
        query_tokens = set(words)
        codes = code_tokens(question)
        result = []
        for index, row in enumerate(rows):
            overlap = len(query_tokens & set(tokens(row["title"] + " " + row["scope"] + " " + row["text"])))
            lexical_rank = lexical.get(row["id"])
            if not lexical_rank:
                continue
            score = 1 / (60 + lexical_rank)
            result.append({**row, "semantic": 0.0, "hybrid": score, "overlap": overlap, "lexical_only": True,
                           "code_hit": code_hits(codes, row["text"])})
        return sorted(result, key=lambda r: (-r["code_hit"], -r["hybrid"], -r["overlap"], r["id"]))
