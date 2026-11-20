"""Centralized text normalization for speech synthesis (TTS)."""
import re
import unicodedata

# Emoji and decorative symbol ranges
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002700-\U000027BF"  # dingbats
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U00002600-\U000026FF"  # misc symbols
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0001FA00-\U0001FA6F"  # chess symbols
    "\U0001FA70-\U0001FAFF"  # symbols extended
    "]+",
    flags=re.UNICODE,
)

# Markdown formatting removal
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_MD_CODE = re.compile(r"`([^`]+)`")
_MD_HEADER = re.compile(r"^#{1,6}\s*", re.MULTILINE)
_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MD_LIST = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_MD_NUMBERED = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)

# Elementos que se ven bien en pantalla pero suenan mal en voz
# Las fuentes ya se anuncian aparte ("Consulta las N fuentes en pantalla").
_SOURCE_TAG = re.compile(r"\s*\[Fuente:[^\]]*\]", re.IGNORECASE)
# Línea con el texto literal del manual (normalmente en inglés) tras la traducción.
_DOCUMENT_TEXT_LINE = re.compile(r"^\s*Texto del documento:.*$", re.MULTILINE)
_BARE_URL = re.compile(r"\(?\bhttps?://\S+?\)?(?=[\s,;]|[.!?](?:\s|$)|$)")
_QUOTES = re.compile(r"[«»“”„\"]")
_TABLE_PIPE = re.compile(r"[ \t]*\|[ \t]*")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}.*$", re.MULTILINE)


def normalize_for_speech(text: str) -> str:
    """Normalize text for TTS: remove emojis, markdown, handle URLs/citations."""
    if not text:
        return ""

    # Remove emojis and decorative symbols
    text = _EMOJI_RE.sub("", text)

    # Remove markdown formatting but keep content
    text = _MD_BOLD.sub(r"\1", text)
    text = _MD_ITALIC.sub(r"\1", text)
    text = _MD_CODE.sub(r"\1", text)
    text = _MD_HEADER.sub("", text)
    text = _MD_LIST.sub("", text)
    text = _MD_NUMBERED.sub("", text)

    # Handle markdown links: keep text, append URL spoken form
    def _link_repl(m):
        link_text = m.group(1)
        url = m.group(2)
        if url.startswith("http"):
            return f"{link_text}"
        return link_text
    text = _MD_LINK.sub(_link_repl, text)

    # Handle citation brackets like [id] - keep as spoken reference
    # e.g. [b707569d-...] -> "referencia"
    text = re.sub(r"\[[a-f0-9-]{8,}\]", "referencia", text)

    # Fuentes, texto literal duplicado, URLs sueltas, comillas y tablas
    text = _SOURCE_TAG.sub("", text)
    text = _DOCUMENT_TEXT_LINE.sub("", text)
    text = _BARE_URL.sub("", text)
    text = _QUOTES.sub("", text)
    text = _TABLE_RULE.sub("", text)
    text = _TABLE_PIPE.sub(", ", text)

    # Cada línea de una lista se vuelve una oración, para que Piper haga pausa.
    lines = [line.strip(" ,") for line in text.split("\n")]
    lines = [line for line in lines if line]
    text = " ".join(line if line[-1] in ".!?:;" else line + "." for line in lines)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    text = re.sub(r"([.!?])\.", r"\1", text)

    # Collapse multiple whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)

    # Trim
    text = text.strip()

    return text