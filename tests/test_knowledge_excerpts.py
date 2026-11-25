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


def test_clean_excerpt_reads_like_text():
    excerpt = clean_excerpt([{"text": PAGE_14, "page": 14}])
    assert excerpt.startswith("4. REQUIRED PERIPHERALS") is False
    assert "The only labels allowed" in excerpt
    assert "\n13" not in excerpt and "Reviewer" not in excerpt
