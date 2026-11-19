"""What the voice reads of a long assistant answer (the ``speech`` field of /api/v1/assistant/chat).

A procedure list is read as its title, its number of steps and its first steps; any other long answer as its first
sentences. Both end with "El resto está en el chat." when something is left out. Sources, links and markdown are
never read. Short answers get no ``speech`` (None): the app reads the answer itself. Pure and deterministic.
"""
import re

MAX_SPEECH = 450      # characters read for a list (all its steps when they fit)
SUMMARY_CHARS = 300   # first sentences of any other long answer
LIST_STEPS = 2        # steps read when the whole list does not fit
STEP_CHARS = 160
REST = "El resto está en el chat."

_CITATION = re.compile(r"[ \t]*\[Fuente:[^\]]*\]")
_SOURCES_BLOCK = re.compile(r"(?ms)^[ \t]*Fuentes(?: públicas)? consultadas.*\Z")
_OFFICIAL = re.compile(r"(?m)^[ \t]*Fuente oficial:.*$")
_LINK = re.compile(r"\[([^\]]+)\]\(https?://[^)\s]*\)")
_URL = re.compile(r"https?://\S+")
_MARKDOWN = re.compile(r"\*\*|__|`|^#+\s*", re.MULTILINE)
_LEADERS = re.compile(r"[.․…]{3,}")
_LIST_HEADER = re.compile(r"^«(?P<title>[^»]+)»\s*\((?P<count>\d+)\s+pasos?\):$")
_PROCEDURE = re.compile(r"^El procedimiento «(?P<title>[^»]+)» tiene (?P<count>\d+) pasos?:\s*(?P<body>.+)$", re.S)
_ITEM = re.compile(r"^(\d{1,2})[.)]\s+(.+)$")
_PROCEDURE_ITEM = re.compile(r"(?:^|\s)\d{1,2}\)\s+")
_INCLUDES = re.compile(r"\s*\(incluye:[^)]*\)")
_SENTENCE_END = re.compile(r"(?<=[.!?…:])\s+")
# "En «X» no identifiqué esa sección. [¿Te refieres a «Y»?] Tiene: A, B, C. ¿Cuál quieres?"
_SECTIONS = re.compile(r"^(?P<lead>En «[^»]+» no identifiqué esa sección\.)(?P<suggestion> ¿Te refieres a «[^»]+»\?)?"
                       r" Tiene: (?P<list>.+)\. ¿Cuál quieres\?$", re.S)


def _clean(answer):
    text = _SOURCES_BLOCK.sub("", answer or "")
    text = _CITATION.sub("", text)
    text = _OFFICIAL.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _URL.sub("", text)
    text = _MARKDOWN.sub("", text)
    return _LEADERS.sub(" ", text)


def _pasos(count):
    return "1 paso" if count == 1 else f"{count} pasos"


def _sentence(text):
    text = _INCLUDES.sub("", text).strip()
    if len(text) > STEP_CHARS:
        text = text[:STEP_CHARS].rsplit(" ", 1)[0].rstrip(",;:")
    text = text.rstrip(".:;")
    return text + "."


def _list_speech(title, count, items):
    head = f"«{title}» tiene {_pasos(count)}."
    every = " ".join([head, *(f"Paso {n}: {_sentence(t)}" for n, t in enumerate(items, start=1))])
    if len(items) >= count and len(every) <= MAX_SPEECH:
        return every
    shown = " ".join(f"Paso {n}: {_sentence(t)}" for n, t in enumerate(items[:LIST_STEPS], start=1))
    return " ".join(part for part in (head, shown, REST) if part)


def _first_sentences(text, limit):
    spoken = ""
    for sentence in _SENTENCE_END.split(text):
        candidate = (spoken + " " + sentence).strip()
        if len(candidate) > limit:
            break
        spoken = candidate
    if not spoken:  # one very long first sentence
        spoken = text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "."
    return spoken


_NOTICE = re.compile(r"^(No encontré [^\n]+; respondo desde «[^»]+»\.)\n(.+)$", re.S)


def spoken_summary(answer):
    """The text to read aloud, or None when the answer is short enough to be read as it is."""
    notice = _NOTICE.match(answer or "")
    if notice:  # the one-line notice is read first, then the answer (short, or its summary)
        rest = notice.group(2)
        spoken_rest = spoken_summary(rest) or re.sub(r"\s+", " ", _clean(rest)).strip()
        return f"{notice.group(1)} {spoken_rest}".strip()
    text = _clean(answer)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    sections = _SECTIONS.match(" ".join(lines))
    if sections:  # never read the list aloud: it stays in the chat
        count = len([item for item in sections.group("list").split(", ") if item.strip()])
        question = sections.group("suggestion") or " ¿Cuál quieres?"
        return f"{sections.group('lead')}{question} Las {count} secciones están en el chat."
    header = _LIST_HEADER.match(lines[0])
    if header:
        items = []
        for line in lines[1:]:
            item = _ITEM.match(line)
            if not item:
                break  # first block only
            items.append(item.group(2))
        return _list_speech(header.group("title"), int(header.group("count")), items)
    procedure = _PROCEDURE.match(" ".join(lines))
    if procedure:
        items = [part for part in _PROCEDURE_ITEM.split(procedure.group("body")) if part.strip()]
        return _list_speech(procedure.group("title"), int(procedure.group("count")), items)
    flat = re.sub(r"\s+", " ", " ".join(lines)).strip()
    if len(flat) <= MAX_SPEECH:
        return None
    return _first_sentences(flat, SUMMARY_CHARS) + " " + REST
