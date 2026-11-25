"""Frases limpias de la evidencia de un manual: sin portadas, índices, números de página sueltos, historial de
revisión ni viñetas; partidas en frases con su página."""
from backend.app.knowledge.excerpts import MAX_SENTENCE_CHARS, MAX_SENTENCES, clean_excerpt, sentences

PAGE_14 = """13

4. REQUIRED PERIPHERALS
1. All computers need the following components to set up a complete workstation:
a. If you prepare a laptop computer, you will need only one extra monitor.
5. The only labels allowed to paste on devices are those used to identify the hostname.

Review
History
Version Description
Revision
Date Reviewer
1.0 Initial Version Apr-26 Jane Doe"""
PAGE_38 = """37
➢ To mute the microphone, open Windows Explorer, search for "Change system sounds".
➢ Now, select the "Recording" section.
➢ Right click on "Microphone Array\""""
COVER = "Training setup\nGuideline\nIT DEPARTMENT"
INDEX = "INDEX\n1. COMPUTER PREPARATION ........ Page 2\n2. WINDOWS DEPLOYMENT ........ Page 2 - 4"


def texts(rows):
    return [item["text"] for item in sentences(rows)]


def test_page_numbers_review_history_and_bullets_are_dropped():
    found = texts([{"text": PAGE_14, "page": 14}, {"text": PAGE_38, "page": 38}])
    assert "The only labels allowed to paste on devices are those used to identify the hostname." in found
    assert 'To mute the microphone, open Windows Explorer, search for "Change system sounds".' in found
    assert 'Right click on "Microphone Array"' in found
    assert not any(text.strip().isdigit() for text in found)
    assert not any("Review" in text or "Reviewer" in text or "Initial Version" in text for text in found)
    assert not any(text.startswith(("➢", "•")) for text in found)


def test_every_sentence_keeps_its_page_and_a_unique_id():
    items = sentences([{"text": PAGE_14, "page": 14}, {"text": PAGE_38, "page": 38}])
    assert [item["id"] for item in items] == list(range(len(items)))
    assert {item["page"] for item in items} == {14, 38}


def test_cover_and_index_pages_are_dropped_when_there_is_real_text():
    found = texts([{"text": COVER, "page": 1}, {"text": INDEX, "page": 2}, {"text": PAGE_14, "page": 14}])
    assert not any("IT DEPARTMENT" in text or "Page 2" in text for text in found)
    assert found


def test_sentences_split_on_end_marks_and_lines():
    found = texts([{"text": "Confirm the speed. It must be higher than 2.20 GHz! Use a 65W adapter", "page": 55}])
    assert found == ["Confirm the speed.", "It must be higher than 2.20 GHz!", "Use a 65W adapter"]


def test_limits():
    long_text = "\n".join(f"Sentence number {n} is here." for n in range(100))
    items = sentences([{"text": long_text, "page": 3}, {"text": "x" * 1000, "page": 4}])
    assert len(items) == MAX_SENTENCES
    assert all(len(item["text"]) <= MAX_SENTENCE_CHARS for item in items)


import asyncio  # noqa: E402
import json  # noqa: E402

import httpx  # noqa: E402

from backend.app.knowledge.grounding import EvidenceSelector  # noqa: E402

CANDIDATES = [{"id": 0, "text": "Load paper in tray two.", "page": 3},
              {"id": 1, "text": "The only labels allowed are hostname labels.", "page": 14},
              {"id": 2, "text": "Clean the rollers monthly.", "page": 5}]


def pick(content=None, status=200, error=None, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(json.loads(request.content))
        if error:
            raise error
        return httpx.Response(status, json={"done": True, "message": {"content": content}})
    selector = EvidenceSelector("qwen-test", transport=httpx.MockTransport(handler))
    return asyncio.run(selector.pick_sentences("¿Qué etiquetas se permiten?", CANDIDATES))


def test_pick_sentences_returns_valid_ids_in_document_order():
    seen = []
    assert pick(json.dumps({"ids": ["1"]}), seen=seen) == [1]
    assert pick(json.dumps({"ids": ["2", "0", "2"]})) == [0, 2]
    payload = seen[0]
    assert payload["options"]["temperature"] == 0.0 and payload["options"]["num_predict"] == 64
    assert payload["format"]["required"] == ["ids"]
    assert "The only labels allowed are hostname labels." in payload["messages"][-1]["content"]


def test_pick_sentences_keeps_at_most_three():
    assert len(pick(json.dumps({"ids": ["0", "1", "2", "1"]}))) <= 3


def test_pick_sentences_failures_give_none():
    assert pick(json.dumps({"ids": ["9"]})) is None          # unknown id: nothing is trusted
    assert pick(json.dumps({"ids": []})) is None
    assert pick("no es json") is None
    assert pick(status=500) is None
    assert pick(error=httpx.ReadTimeout("slow")) is None


def test_clean_excerpt_reads_like_text():
    excerpt = clean_excerpt([{"text": PAGE_14, "page": 14}])
    assert excerpt.startswith("4. REQUIRED PERIPHERALS") is False
    assert "The only labels allowed" in excerpt
    assert "\n13" not in excerpt and "Reviewer" not in excerpt
