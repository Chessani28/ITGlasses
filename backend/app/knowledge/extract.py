"""Bounded extraction worker. Invoked only for generated managed file paths."""
from io import BytesIO
from pathlib import Path
import json
import re
import sys
import zipfile

from .models import KnowledgeError

MAX_BYTES = 10 * 1024 * 1024
MAX_TEXT = 500_000
EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}


def validate(data: bytes, extension: str):
    if extension not in EXTENSIONS:
        raise KnowledgeError("unsupported_format", "Use PDF, DOCX, TXT or Markdown.")
    if not data or len(data) > MAX_BYTES:
        raise KnowledgeError("file_size", "Files must contain 1 byte to 10 MiB.", 413)
    if extension == ".pdf" and not data.startswith(b"%PDF-"):
        raise KnowledgeError("invalid_file", "The file is not a PDF.")
    if extension == ".docx":
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                entries = archive.infolist()
                if (len(entries) > 1000 or sum(e.file_size for e in entries) > 50 * 1024 * 1024
                        or "word/document.xml" not in archive.namelist()
                        or any("vbaProject" in e.filename for e in entries)):
                    raise ValueError()
        except (ValueError, zipfile.BadZipFile):
            raise KnowledgeError("invalid_file", "Invalid or oversized DOCX container.") from None


def extract(data: bytes, extension: str) -> list[dict]:
    validate(data, extension)
    blocks = []
    if extension == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise KnowledgeError("encrypted_pdf", "Encrypted PDFs are not supported.")
        if len(reader.pages) > 300:
            raise KnowledgeError("page_limit", "PDFs are limited to 300 pages.")
        for number, page in enumerate(reader.pages, 1):
            blocks.append({"text": page.extract_text() or "", "page": number, "section": f"Page {number}", "paragraph": 1})
    elif extension == ".docx":
        from docx import Document
        from docx.text.paragraph import Paragraph
        document = Document(BytesIO(data))
        section = "Document"
        for number, item in enumerate(document.iter_inner_content(), 1):
            if isinstance(item, Paragraph):
                if item.style and item.style.name.startswith("Heading"):
                    section = item.text
                text = item.text
            else:
                text = "\n".join(" | ".join(cell.text for cell in row.cells) for row in item.rows)
            blocks.append({"text": text, "page": None, "section": section, "paragraph": number})
    else:
        text = data.decode("utf-8-sig")
        if "\x00" in text:
            raise ValueError("binary content")
        section = "Document"
        for number, paragraph in enumerate(re.split(r"\n\s*\n", text), 1):
            lines = paragraph.splitlines()
            for line in lines:
                if extension == ".md" and re.match(r"^#{1,6}\s+", line):
                    section = re.sub(r"^#{1,6}\s+", "", line).strip()
            blocks.append({"text": paragraph, "page": None, "section": section, "paragraph": number})
    if sum(len(b["text"]) for b in blocks) > MAX_TEXT:
        raise KnowledgeError("text_limit", "Extracted text exceeds 500000 characters.")
    result = [b for b in blocks if b["text"].strip()]
    if not result:
        raise KnowledgeError("no_text", "No extractable text. Scanned PDFs require OCR, which is not installed.")
    return result


def chunks(blocks: list[dict], limit: int = 900) -> list[dict]:
    result = []
    for block in blocks:
        text = block["text"].strip()
        start = 0
        while start < len(text):
            end = min(start + limit, len(text))
            if end < len(text):
                boundary = max(text.rfind(". ", start + limit // 2, end), text.rfind("\n", start + limit // 2, end))
                if boundary != -1:
                    end = boundary + 1
            result.append({**block, "text": text[start:end], "offset": start})
            start = end
    return result


if __name__ == "__main__":
    try:
        path = Path(sys.argv[1])
        result = {"blocks": extract(path.read_bytes(), path.suffix.lower())}
    except KnowledgeError as exc:
        result = {"error": exc.code, "message": exc.message}
    except Exception:
        result = {"error": "invalid_file", "message": "The document could not be extracted safely."}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
