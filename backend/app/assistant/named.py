"""Deterministic resolution of the document or procedure a question names.

The index holds the titles of the authorized documents and the headings of their procedures. A name matches when
its content words appear in an entry (or the entry's words in the name), comparing without accents, plurals, the
generic words (procedimiento, manual, guía, guide...) and through a small Spanish->English glossary for the
headings written in English. Otherwise the best entry above NEAREST_MIN is offered as the closest one: one name,
never a list. No model is called.
"""
from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata

MATCH_MIN = 0.80     # string similarity that still counts as the same name (misspellings, word order)
NEAREST_MIN = 0.45   # below this nothing is offered as the closest one
_TOKEN = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset("de del la el los las a al y e en para por con sobre un una the of for and to on in".split())
GENERIC = frozenset(
    "procedimiento proceso manual documento guia instructivo protocolo procedure process guide guideline handbook "
    "policy document".split()
)
# Spanish words of a question -> the English words of the headings they translate. Singular, without accents.
# The only glossary: keep it small and covered by tests.
GLOSSARY = {
    "despliegue": ("deployment",),
    "implementacion": ("deployment",),
    "preparacion": ("preparation",),
    "instalacion": ("installation",),
    "periferico": ("peripheral",),
    "requerido": ("required",),
    "reinicio": ("restart", "reboot"),
    "frecuencia": ("frecuency", "frequency"),
    "estacion": ("station",),
    "computadora": ("computer",),
    "equipo": ("computer",),
    "aplicacion": ("app", "application"),
}
# Spanish names of the English headings, one each, for people who do not read English. They match as the heading
# and go into the Whisper vocabulary (Voz 2) when the heading is in an authorized document. The only alias table.
ALIASES = {
    "computer preparation": "preparación de la computadora",
    "windows deployment procedure": "despliegue de Windows",
    "graphene stations deployment procedure": "despliegue de estaciones Graphene",
    "required peripherals": "periféricos requeridos",
    "apps installation": "instalación de aplicaciones",
    "confirms the cpu frecuency": "frecuencia del CPU",
}
# Spanish nicknames of the documents with an English title. They match as the title (never offered as the closest
# name) and go into the Whisper vocabulary before the heading aliases.
DOCUMENT_ALIASES = {
    "training setup guideline": ("manual de entrenamiento", "guía de capacitación"),
}
SUGGEST_MIN = 0.70     # a heading this close is offered as "¿Te refieres a «X»?"
SUGGEST_MARGIN = 0.05  # ...only when it stands out from the second closest


def alias_of(heading):
    """The Spanish alias of a heading, or None."""
    return ALIASES.get(" ".join(fold(heading or "").split()))


def nicknames_of(title):
    """The Spanish nicknames of a document title (possibly empty)."""
    return DOCUMENT_ALIASES.get(" ".join(fold(title or "").split()), ())


def fold(text):
    text = unicodedata.normalize("NFKD", (text or "").casefold())
    return "".join(c for c in text if not unicodedata.combining(c))


def _singular(token):
    if len(token) > 5 and token.endswith("es") and token[-3] in "nrldz":
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def content_tokens(text):
    """Content words of a name: folded, singular, without stopwords or generic document words."""
    tokens = (_singular(t) for t in _TOKEN.findall(fold(text)))
    return tuple(t for t in tokens if t not in STOPWORDS and t not in GENERIC)


def _variants(token):
    return {token, *GLOSSARY.get(token, ())}


def score(name, entry):
    """1.0: same content words; 0.98: every word of the name is in the entry; 0.95: every word of the entry is in
    the name; otherwise the similarity of the content words (0..1). Glossary words count as the same word."""
    name_tokens, entry_tokens = content_tokens(name), content_tokens(entry)
    if not name_tokens or not entry_tokens:
        return 0.0
    entry_set = set(entry_tokens)
    if all(_variants(token) & entry_set for token in name_tokens):
        return 1.0 if len(set(name_tokens)) == len(entry_set) else 0.98
    name_expanded = set().union(*(_variants(token) for token in name_tokens))
    if entry_set <= name_expanded:
        return 0.95
    return SequenceMatcher(None, " ".join(name_tokens), " ".join(entry_tokens)).ratio()


@dataclass(frozen=True)
class Entry:
    text: str
    document_id: str
    document_title: str
    procedure: str | None  # None for a document title
    alias: bool = False    # a Spanish alias: it matches, but is never offered as the closest name


@dataclass(frozen=True)
class Resolution:
    status: str                    # "found", "missing" or "ambiguous"
    document_id: str | None = None
    document_title: str | None = None
    procedure: str | None = None   # the named (or only) procedure of that document
    nearest: str | None = None     # "missing": the closest title or heading, if any
    suggestion: str | None = None  # "found" without a procedure: a heading close to what the question says


def build_index(documents, procedures_of):
    """Entries for the authorized documents: ``documents`` are dicts with id and title, ``procedures_of(id)``
    the procedure list of a document."""
    entries, counts = [], {}
    for document in documents:
        procedures = [p for p in (procedures_of(document["id"]) or []) if p.get("title")]
        counts[document["id"]] = [p["title"] for p in procedures]
        entries.append(Entry(document["title"], document["id"], document["title"], None))
        entries.extend(Entry(nickname, document["id"], document["title"], None, alias=True)
                       for nickname in nicknames_of(document["title"]))
        for procedure in procedures:
            entries.append(Entry(procedure["title"], document["id"], document["title"], procedure["title"]))
            if alias_of(procedure["title"]):
                entries.append(Entry(alias_of(procedure["title"]), document["id"], document["title"],
                                     procedure["title"], alias=True))
    return tuple(entries), counts


HEADING_FUZZY_MIN = 0.85


def _covers(window, heading):
    """How well a span of the question names a heading: 1.0 when every word of the heading is in the span (glossary
    included), otherwise the similarity of the words ("Require Peripherals" ~ REQUIRED PERIPHERALS)."""
    heading_tokens = content_tokens(heading)
    if not window or not heading_tokens:
        return 0.0
    expanded = set().union(*(_variants(token) for token in window))
    if set(heading_tokens) <= expanded:
        return 1.0
    return SequenceMatcher(None, " ".join(window), " ".join(heading_tokens)).ratio()


def heading_scores(message, headings):
    """{heading: how well the question names it}: the best span of the question's content words, compared with the
    heading and with its Spanish alias. Spans are as long as the name or one word longer and must cover it."""
    words = content_tokens(message)
    scores = {}
    for heading in headings:
        best = 0.0
        for name in (heading, alias_of(heading)):
            size = len(content_tokens(name)) if name else 0
            if not size:
                continue
            for length in (size, size + 1):
                for start in range(0, max(0, len(words) - length) + 1):
                    best = max(best, _covers(words[start:start + length], name))
        scores[heading] = best
    return scores


def find_heading(message, headings):
    """The heading a question mentions, also misspelled or by its Spanish alias: (heading, score) or None.

    The span must cover the heading (every word, or a similarity of HEADING_FUZZY_MIN or more), so one shared word
    ("equipo" -> computer) never selects COMPUTER PREPARATION.
    """
    scores = heading_scores(message, headings)
    best = max(scores.items(), key=lambda item: item[1], default=None)
    return best if best and best[1] >= HEADING_FUZZY_MIN else None


def closest_heading(message, headings):
    """A heading worth offering as "¿Te refieres a «X»?" (SUGGEST_MIN or more and ahead of the rest), or None."""
    ranked = sorted(heading_scores(message, headings).items(), key=lambda item: -item[1])
    if not ranked or ranked[0][1] < SUGGEST_MIN:
        return None
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < SUGGEST_MARGIN:
        return None
    return ranked[0][0]


def find_heading_anywhere(message, index):
    """Resolution of the only heading (or heading alias) the question names in any authorized document, or None.

    The heading must be covered (HEADING_FUZZY_MIN or more) and be the only (document, heading) pair that is: a
    heading repeated in two documents, or two headings named, give None.
    """
    entries, _ = index
    pairs = {(entry.document_id, entry.procedure): entry.document_title for entry in entries if entry.procedure}
    scores = heading_scores(message, sorted({heading for _, heading in pairs}))
    named_pairs = [pair for pair in pairs if scores.get(pair[1], 0.0) >= HEADING_FUZZY_MIN]
    if len(named_pairs) != 1:
        return None
    (document_id, heading), = named_pairs
    return Resolution("found", document_id, pairs[(document_id, heading)], heading)


def resolve(name, index, message=None):
    """Resolution of a name against an index from build_index.

    With the whole ``message``: a named document without a named procedure takes the heading the question mentions
    (find_heading), and a missing name offers as the closest one the document of a heading the question mentions
    ("Según el manual de Terry D., qué dice REQUIRED PERIPHERALS" -> «Training Setup Guideline»).
    """
    entries, procedures = index
    scored = sorted(((score(name, entry.text), entry) for entry in entries), key=lambda item: -item[0])
    matches = [(value, entry) for value, entry in scored if value >= MATCH_MIN]
    if not matches:
        mentioned = find_heading(message, [e.text for e in entries if e.procedure and not e.alias]) if message else None
        if mentioned:
            nearest = next(e.document_title for e in entries if e.procedure and e.text == mentioned[0])
        else:
            nearest = next((entry.text for value, entry in scored if value >= NEAREST_MIN and not entry.alias), None)
        return Resolution("missing", nearest=nearest)
    best = matches[0][0]
    top = [entry for value, entry in matches if value == best]
    documents = {entry.document_id for entry in top}
    if len(documents) > 1:
        return Resolution("ambiguous")
    document_id = top[0].document_id
    headings = list(dict.fromkeys(entry.procedure for entry in top if entry.procedure))
    if len(headings) == 1:
        procedure = headings[0]
    elif not headings and len(procedures.get(document_id, [])) == 1:
        procedure = procedures[document_id][0]
    elif not headings and message:
        mentioned = find_heading(message, procedures.get(document_id, []))
        procedure = mentioned[0] if mentioned else None
    else:
        procedure = None
    suggestion = None
    if procedure is None and message:
        suggestion = closest_heading(message, procedures.get(document_id, []))
    return Resolution("found", document_id, top[0].document_title, procedure, suggestion=suggestion)


def not_found_message(target, nearest):
    """'No encontré un procedimiento llamado «X»; lo más cercano es «Y».' with the noun the question used."""
    article, called = ("una", "llamada") if target.get("feminine") else ("un", "llamado")
    answer = f"No encontré {article} {target['noun']} {called} «{target['name']}»"
    if nearest:
        return answer + f"; lo más cercano es «{nearest}»."
    return answer + " en los documentos autorizados."
