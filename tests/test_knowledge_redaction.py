"""Secretos en los manuales: nunca salen del índice (ni al modelo, ni al chat, ni a la voz). Todos los secretos de
estas pruebas son inventados."""
import asyncio
import sqlite3
from contextlib import closing

import pytest

from backend.app.knowledge.models import KnowledgeRequest, Metadata
from backend.app.knowledge.redaction import HIDDEN, redact
from backend.app.knowledge.service import KnowledgeService
from test_knowledge_bilingual import EnglishOnlyEmbeddings, PickingSelector

FAKE_HEX = "0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF"
FAKE_B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567ABCDEFGHIJKLMNOP"


@pytest.mark.parametrize("text,secret", [
    ("ACCESS_KEY_ID=ABCD1234EFGH5678 REGION_TYPE=na1", "ABCD1234EFGH5678"),
    ("ACCESS_KEY_SECRET=zyx987wvu654tsr321", "zyx987wvu654tsr321"),
    ("password: Hola1234", "Hola1234"),
    ("Contraseña = Secreta.2026", "Secreta.2026"),
    ("api_key: sk-test-1234567890", "sk-test-1234567890"),
    ("client_secret=abc123def456", "abc123def456"),
    (f"Enter the Authentication Token:\n\n{FAKE_HEX}\n6C15513AF9AD7EC1856D32661F9BD78E0DA80\n\nFinish",
     FAKE_HEX),
    (f"msiexec /i Agent.msi KEY={FAKE_B32}\nUA==== /quiet", FAKE_B32),
])
def test_secrets_are_hidden(text, secret):
    hidden, count = redact(text)
    assert secret not in hidden
    assert HIDDEN in hidden and count >= 1


def test_the_second_line_of_a_wrapped_token_is_hidden_too():
    hidden, _ = redact(f"Token:\n{FAKE_HEX}\n6C15513AF9AD7EC1856D32661F9BD78E0DA80")
    assert "6C15513AF9AD7EC1856D32661F9BD78E0DA80" not in hidden


def test_base32_padding_left_over_is_hidden():
    hidden, _ = redact(f"KEY={FAKE_B32}\nUA====")
    assert "UA====" not in hidden


@pytest.mark.parametrize("text", [
    "Press the power on button and press F12 until you enter to boot setup.",
    "Confirm the speed value, this must be higher than 2.20 GHz.",
    r"Press Windows+R and paste \\AGSVNMFL02\Public\Centris IT\Apps\Westmed\Verint",
    "Run VC_redist x86 as administrator, then ScreenAgent-64bit-3.0.37.msi.",
    "Connect to V03-RIS-01-701.five9-wfo.com:29436 and press Next.",
    "Procedimiento 10: Reinicio de servidor. Pasos: 1. Apagar el servidor. 2. Esperar 30 segundos.",
    "La contraseña debe tener al menos 12 caracteres.",
    "Verify the user credentials (username and password).",
])
def test_normal_manual_text_is_untouched(text):
    assert redact(text) == (text, 0)


MANUAL = f"Reception printer\nUse only the HP 58A toner cartridge.\nAdmin password: Hola1234\nToken:\n{FAKE_HEX}"


def build(tmp_path):
    svc = KnowledgeService(tmp_path, EnglishOnlyEmbeddings(), PickingSelector(picked=(0, 1, 2)))
    asyncio.run(svc.ingest("printer.md", MANUAL.encode(), Metadata(
        title="Reception Printer Guide", procedure_id="77", version="1", authorized=True)))
    return svc


def test_a_new_manual_is_stored_without_its_secrets(tmp_path):
    svc = build(tmp_path)
    with closing(sqlite3.connect(svc.store.path)) as db:
        stored = " ".join(row[0] for row in db.execute("SELECT text FROM chunks"))
    assert "Hola1234" not in stored and FAKE_HEX not in stored and "toner cartridge" in stored


def test_the_chunk_size_is_configurable(tmp_path, monkeypatch):
    from backend.app.knowledge.service import chunk_chars
    monkeypatch.delenv("ITGLASSES_CHUNK_CHARS", raising=False)
    default = chunk_chars()
    monkeypatch.setenv("ITGLASSES_CHUNK_CHARS", "120")
    assert chunk_chars() == 120
    monkeypatch.setenv("ITGLASSES_CHUNK_CHARS", "5")  # nonsense is ignored
    assert chunk_chars() == default
    monkeypatch.setenv("ITGLASSES_CHUNK_CHARS", "120")
    text = "\n".join(f"Sentence number {n} of a long manual page about printers." for n in range(12))
    svc = KnowledgeService(tmp_path, EnglishOnlyEmbeddings(), PickingSelector())
    asyncio.run(svc.ingest("long.md", text.encode(), Metadata(title="Long", procedure_id="5", version="1",
                                                              authorized=True)))
    sizes = [len(row["text"]) for row in svc.store.eligible_chunks()]
    assert len(sizes) > 3 and max(sizes) <= 120


def test_a_manual_indexed_before_redaction_never_returns_its_secrets(tmp_path):
    svc = build(tmp_path)
    with closing(sqlite3.connect(svc.store.path)) as db:  # as if it had been indexed by an older version
        db.execute("UPDATE chunks SET text=? ", (MANUAL,))
        db.commit()
    texts = " ".join(row["text"] for row in svc.store.eligible_chunks())
    texts += " ".join(row["text"] for row in svc.store.document_chunks(svc.store.documents()[0]["id"]))
    assert "Hola1234" not in texts and FAKE_HEX not in texts
    result = asyncio.run(svc.query(KnowledgeRequest(message="Which cartridge do I use for the reception printer?")))
    assert "Hola1234" not in result.model_dump_json() and FAKE_HEX not in result.model_dump_json()
