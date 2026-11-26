import asyncio
from io import BytesIO
import numpy as np
import pytest
from docx import Document
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, NumberObject, DecodedStreamObject

from backend.app.knowledge.extract import extract, chunks
from backend.app.knowledge.models import KnowledgeError, Metadata
from backend.app.knowledge.service import KnowledgeService


class FakeEmbeddings:
    model = "synthetic-test-only"
    async def fingerprint(self): return "synthetic-v1"
    async def embed(self, texts, query=False):
        return np.asarray([[1., 0., 0.] for text in texts], dtype=np.float32)
    async def unload(self): pass


def pdf_bytes():
    writer = PdfWriter()
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    for label in ['Synthetic procedure page one', 'Synthetic procedure page two']:
        page = writer.add_blank_page(612, 792)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(f'BT /F1 12 Tf 72 720 Td ({label}) Tj ET'.encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    output = BytesIO(); writer.write(output)
    return output.getvalue()


def test_pdf_pages():
    blocks = extract(pdf_bytes(), '.pdf')
    assert [b['page'] for b in blocks] == [1, 2]
    assert 'page two' in blocks[1]['text']


def test_docx_headings_tables():
    document = Document(); document.add_heading('Synthetic heading', 1)
    document.add_paragraph('Documented text.')
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = 'Key'; table.cell(0, 1).text = 'Value'
    output = BytesIO(); document.save(output)
    blocks = extract(output.getvalue(), '.docx')
    assert blocks[1]['section'] == 'Synthetic heading'
    assert blocks[0]['page'] is None
    assert 'Key | Value' in blocks[-1]['text']


@pytest.mark.parametrize('extension', ['.txt', '.md'])
def test_text_formats(extension):
    blocks = extract('# Overview\n\nSynthetic procedure.'.encode(), extension)
    assert 'Synthetic procedure.' == blocks[-1]['text']
    assert blocks[-1]['page'] is None
    if extension == '.md': assert blocks[-1]['section'] == 'Overview'


@pytest.mark.parametrize('data,extension', [(b'', '.txt'), (b'test', '.exe'), (b'bad', '.pdf'), (b'bad', '.docx')])
def test_invalid_uploads(data, extension):
    with pytest.raises(KnowledgeError): extract(data, extension)


def test_chunk_coverage():
    text = 'A sentence. ' * 200
    pieces = chunks([dict(text=text, page=2, section='S', paragraph=1)])
    assert ''.join(c['text'] for c in pieces) == text.strip()
    assert all(len(c['text']) <= 900 and c['page'] == 2 for c in pieces)


def test_revisions_and_access(tmp_path):
    async def scenario():
        service = KnowledgeService(tmp_path, FakeEmbeddings(), None)
        metadata = Metadata(title='Synthetic', authorized=True, procedure_id='024')
        first = await service.ingest('../escape.txt', b'First version.', metadata)
        old_source = service.store.eligible_chunks()[0]['id']
        second = await service.ingest('next.txt', b'Second version.', metadata.model_copy(update={'version': '2'}), first['id'])
        assert first['id'] == second['id'] and second['current_revision'] == 2
        assert second['procedure_id'] == '24'
        assert service.store.evidence(old_source) is None
        assert len(service.store.eligible_chunks()) == 1
        assert len(list((tmp_path / 'documents/managed').rglob('source.txt'))) == 2
        await service.access(first['id'], authorized=False)
        assert not service.store.eligible_chunks()
        await service.access(first['id'], authorized=True)
        await service.access(first['id'], withdrawn=True)
        assert not service.store.eligible_chunks()
    asyncio.run(scenario())


def test_a_chunk_never_ends_on_a_step_number():
    # 2026-11-26, test manuals: the cut was made after "4. " (taken for the end of a sentence), so the number stayed
    # in one piece and the step's text in the next one, and the procedure lost that step.
    lines = [f"{n}. Paso número {n} del procedimiento de prueba con texto suficiente para llenar la línea." for n in range(1, 40)]
    text = "\n".join(lines)
    pieces = chunks([dict(text=text, page=1, section='S', paragraph=1)])
    assert ''.join(c['text'] for c in pieces) == text
    for piece in pieces[:-1]:
        assert piece['text'].endswith('\n'), repr(piece['text'][-40:])
    for piece in pieces[1:]:
        assert re_step.match(piece['text']), repr(piece['text'][:40])


def test_without_lines_a_chunk_still_ends_at_a_sentence_not_at_a_number():
    text = ("Primera oración del texto. " * 20) + "Siga el paso 4. Luego revise todo. " + ("Otra oración más. " * 30)
    pieces = chunks([dict(text=text, page=1, section='S', paragraph=1)])
    assert ''.join(c['text'] for c in pieces) == text.strip()
    for piece in pieces[:-1]:
        assert not piece['text'].rstrip().endswith(("4.", "paso 4.")), repr(piece['text'][-30:])


import re as _re
re_step = _re.compile(r"^\d+\. ")


def test_a_manual_with_several_numbered_procedures_is_indexed(tmp_path):
    # 2026-11-26: "Procedimiento 1: …", "Procedimiento 2: …" in one manual was refused with "Multiple procedure
    # identifiers were found" (procedure_metadata_required). It is indexed without an automatic procedure id.
    async def scenario():
        service = KnowledgeService(tmp_path, FakeEmbeddings(), None)
        text = (b"Manual de prueba.\nProcedimiento 1: Preparar el equipo\n1. Encender.\n2. Revisar.\n"
                b"Procedimiento 2: Entregar el equipo\n1. Firmar.\n2. Cerrar el ticket.\n")
        document = await service.ingest('manual.txt', text, Metadata(title='Manual', authorized=True))
        assert document['procedure_id'] is None
        assert len(service.store.eligible_chunks()) >= 1
    asyncio.run(scenario())


def test_a_single_procedure_number_is_still_taken_as_its_id(tmp_path):
    async def scenario():
        service = KnowledgeService(tmp_path, FakeEmbeddings(), None)
        document = await service.ingest('p.txt', b'Procedimiento 024: Reinicio del servidor.\n1. Apagar.',
                                        Metadata(title='Reinicio', authorized=True))
        assert document['procedure_id'] == '24'                  # normalized, as before
    asyncio.run(scenario())
