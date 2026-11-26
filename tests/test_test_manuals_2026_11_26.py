"""Questions about the three test manuals of 2026-11-26 (Grupo Andina, fictitious): what failed and why.

1. An error code of the manual ("¿Qué significa el error C6000?", "¿Qué hago con el código J0511?") answered "no es un
   código reconocido": one shared word (the code) did not pass the relevance gate, and "qué significa" was an
   identification. A code of the question found as such in a piece makes that piece relevant, and asks information.
"""
import asyncio

from test_assistant_service import StubWebSearch

from backend.app.knowledge.models import KnowledgeRequest, Metadata
from backend.app.knowledge.retrieval import code_tokens, intent
from backend.app.knowledge.service import KnowledgeService
from test_knowledge_bilingual import EnglishOnlyEmbeddings, TranslatingSelector

GUIDE = """Network Printer Troubleshooting Guide
Purpose
This guide helps Service Desk technicians solve the most common problems with the office network printers.
Error codes
Code Meaning Action
C0030 Fax/system board error Turn the printer off and on. If it repeats, call PrintCare.
C6000 Fuser heater error Turn the printer off, wait 10 minutes and call PrintCare. Do not keep using it.
J0511 Paper jam in the paper feed Follow section 3 (Paper jam).
"""


def guide(tmp_path, translation="What does the code mean?"):
    svc = KnowledgeService(tmp_path, EnglishOnlyEmbeddings(), TranslatingSelector(translation))
    asyncio.run(svc.ingest("guide.md", GUIDE.encode(), Metadata(title="network-printer-guide", version="1",
                                                                  authorized=True)))
    return svc


def ask(svc, message):
    return asyncio.run(svc.query(KnowledgeRequest(message=message)))


def test_codes_are_words_with_letters_and_digits():
    assert code_tokens("¿Qué significa el error C6000?") == {"c6000"}
    assert code_tokens("¿Qué hago con el código J0511?") == {"j0511"}
    assert code_tokens("La cola PRN-3-A del tercer piso") == {"prn-3-a"}
    assert code_tokens("Tengo 16 GB de RAM y Windows 11") == set()          # a number or a version is not a code


def test_what_a_code_means_is_information_not_an_identification():
    assert intent("¿Qué significa el error C6000?") == "information"
    assert intent("¿Qué es el código J0511?") == "information"
    assert intent("¿Qué es formatear?") == "identify"                       # without a code, as before


def test_a_code_found_in_the_manual_is_answered_from_it(tmp_path):
    svc = guide(tmp_path)
    for question in ("¿Qué significa el error C6000?", "¿Qué hago con el código J0511?"):
        answer = ask(svc, question)
        assert answer.status == "answered" and answer.sources, question
        assert answer.sources[0]["title"] == "network-printer-guide"


def test_a_code_that_is_not_in_the_manual_is_not_answered_from_it(tmp_path):
    answer = ask(guide(tmp_path), "¿Qué significa el error E9999?")
    assert answer.status != "answered"


def test_a_name_inside_the_manual_is_information_not_the_documents_name():
    # "¿Con qué nombre se restaura el archivo?" answered "Documento identificado: «politica-respaldos»".
    for question in ("¿Con qué nombre se restaura el archivo?", "¿Qué nombre lleva la cola de la impresora A?",
                     "¿Cuál es el nombre del equipo de Monterrey?", "¿Qué nombre le pongo a la laptop?"):
        assert intent(question) == "information", question
    assert intent("¿Cómo se llama el proceso? Dame su nombre.") == "identify"
    assert intent("¿Cuál es el nombre del procedimiento para formatear?") == "identify"


# --- 2. Picking the procedure the question is about -----------------------------------------------------------------

LAPTOPS = [{"title": "Preparación física del equipo", "steps": [{"text": "Revisar la caja."}]},
           {"title": "Configuración de Windows y unión al dominio", "steps": [{"text": "Unir al dominio."}]},
           {"title": "Software base", "steps": [{"text": "Instalar el antivirus."}]},
           {"title": "Entrega al colaborador", "steps": [{"text": "Firmar la carta."}]}]
BACKUPS = [{"title": "Restaurar un archivo borrado por un usuario", "steps": [{"text": "Pedir la ruta."}]},
           {"title": "Restaurar un servidor completo", "steps": [{"text": "Declarar un incidente."}]}]


def test_one_shared_word_with_no_rival_picks_that_procedure():
    from backend.app.assistant.service import UnifiedAssistantService as S
    best = S._clearly_best_title(LAPTOPS, "¿Cuáles son los pasos para preparar una laptop nueva?")
    assert best is not None and best["title"] == "Preparación física del equipo"
    assert S._clearly_best_title(LAPTOPS, "¿Cuáles son todos los pasos?") is None          # nothing shared: ask
    assert S._clearly_best_title(BACKUPS, "¿Cómo restauro?") is None                        # two equally close: ask


def test_how_to_with_a_clearly_matching_procedure_reads_its_steps():
    from backend.app.assistant.service import UnifiedAssistantService as S
    best = S._clearly_best_title(BACKUPS, "Un usuario borró un archivo, ¿cómo lo recupero?")
    assert best is not None and best["title"] == "Restaurar un archivo borrado por un usuario"


# --- 3. Spanish answers from an English manual ----------------------------------------------------------------------

def test_a_faithful_translation_keeps_a_menu_path_with_its_arrows():
    from backend.app.assistant.service import UnifiedAssistantService as S
    source = "Print a configuration page from the printer menu (System Menu > Report > Status Page) and confirm the IP address."
    spanish = ("Imprime una página de configuración desde el menú de la impresora (System Menu > Report > Status Page) "
               "y confirma la dirección IP.")
    assert S._clean_translation(source, spanish) == spanish


def test_a_table_row_with_a_code_is_not_taken_for_a_screen_name():
    from backend.app.assistant.service import UnifiedAssistantService as S
    source = "C6000 Fuser heater error Turn the printer off, wait 10 minutes and call PrintCare."
    spanish = "C6000: error del calentador del fusor. Apaga la impresora, espera 10 minutos y llama a PrintCare."
    assert S._clean_translation(source, spanish) == spanish
    # A real name on screen still has to survive.
    assert S._clean_translation("Open the Newly Added folder.", "Abre la carpeta Recién agregados.") == \
        "Open the Newly Added folder."


# --- 4. A short follow-up of a manual answer asks the manual first --------------------------------------------------

def test_a_can_i_follow_up_of_a_manual_answer_asks_the_manual_with_its_context():
    # "¿Cómo saco una hoja atorada?" → "¿Puedo usar unas pinzas de metal?" was answered in general ("sí puedes
    # usarlas…") while the manual says never to use metal tools.
    import uuid as _uuid
    from test_named_reference import Knowledge, build
    from test_user_test_2026_11_26 import ask
    knowledge = Knowledge()
    service, sid = build(knowledge), str(_uuid.uuid4())
    ask(service, "¿Cuáles son los pasos para reiniciar el servidor?", sid)
    asked = len(knowledge.queries)
    answer = ask(service, "¿Puedo usar unas pinzas de metal?", sid)
    assert len(knowledge.queries) == asked + 1                              # the manual was asked
    query = knowledge.queries[-1].message
    assert "pinzas" in query and "reiniciar el servidor" in query           # with the previous question as context
    assert answer["route"] == "knowledge"


def test_a_new_question_after_a_manual_answer_is_not_glued_to_it():
    from backend.app.assistant.service import UnifiedAssistantService as S
    state = {"active_route": "knowledge", "active_document_id": "d", "history": [("¿Cómo saco una hoja atorada?", "…")]}
    assert S._continues_manual(state, "¿Puedo usar unas pinzas de metal?")
    assert S._continues_manual(state, "¿Y si sigue atorada?")
    assert not S._continues_manual(state, "¿Qué es una VPN?")
    assert not S._continues_manual(state, "¿Cuál es la capital de Francia?")
    assert not S._continues_manual({**state, "active_route": "general"}, "¿Puedo usar unas pinzas?")


# The real translations of qwen3:4b for the printer guide (2026-11-26): faithful, but rejected by the guards.
def test_real_translations_of_the_printer_guide_are_accepted():
    from backend.app.assistant.service import UnifiedAssistantService as S
    pairs = [
        ("C6000 Fuser heater error Turn the printer off, wait 10 minutes and call PrintCare.",
         "Error del calentador del fusor C6000 Apaga la impresora, espera 10 minutos y llama a PrintCare."),
        ("Follow section 3 (Paper jam).", "Sigue la sección 3 (Atasco de papel)."),
        ("Every printer is installed through the print server PRINT01, and users print to the shared queues named "
         "PRN-FLOOR-LETTER (for example, PRN-3-A is printer A on the third floor).",
         "Cada impresora está instalada a través del servidor de impresión PRINT01, y los usuarios imprimen en las "
         "colas compartidas nombradas PRN-FLOOR-LETTER (por ejemplo, PRN-3-A es la impresora A del tercer piso)."),
    ]
    for source, spanish in pairs:
        assert S._clean_translation(source, spanish) == spanish, spanish


def test_a_translation_that_renames_a_menu_is_still_rejected():
    from backend.app.assistant.service import UnifiedAssistantService as S
    source = "Print a configuration page from the printer menu (System Menu > Report > Status Page)."
    renamed = "Imprime una página de configuración desde el menú (Menú Sistema > Informe > Página de Estado)."
    assert S._clean_translation(source, renamed) == source     # the technician looks for "System Menu" on screen
    assert S._clean_translation("Note: back up first.", "Por ejemplo, haz un respaldo.") == "Note: back up first."


def test_the_translation_prompt_asks_to_keep_screen_names():
    from backend.app.assistant.service import TRANSLATION_PROMPT
    assert "menús" in TRANSLATION_PROMPT and "sin traducir" in TRANSLATION_PROMPT


# --- 5. Two manuals about the same thing ------------------------------------------------------------------------------

def two_manuals(tmp_path, translation):
    svc = KnowledgeService(tmp_path, EnglishOnlyEmbeddings(), TranslatingSelector(translation))
    for title, text in (("Training Setup Guideline", "Use letter paper in tray 1 for the training printouts."),
                        ("manual-alta-laptops", "Load letter paper in tray 1 before printing the delivery letter.")):
        asyncio.run(svc.ingest(f"{title}.md", text.encode(), Metadata(title=title, version="1", authorized=True)))
    return svc


def test_a_tie_is_broken_by_the_manual_the_question_names(tmp_path):
    # "¿Cuánta RAM… para el training?" answered "Encontré varias coincidencias plausibles" once a second manual
    # about laptops was uploaded.
    answer = ask(two_manuals(tmp_path, "Which paper do I use for the training?"), "¿Qué papel uso para el training?")
    assert answer.status == "answered" and answer.sources[0]["title"] == "Training Setup Guideline"




def test_an_unbroken_tie_names_the_manuals_in_its_question(tmp_path):
    answer = ask(two_manuals(tmp_path, "Which paper do I use?"), "¿Qué papel uso?")
    assert answer.status == "clarification_required"
    assert "Training Setup Guideline" in answer.answer and "manual-alta-laptops" in answer.answer


def test_what_the_user_already_tried_continues_the_manual_answer():
    # "La impresora no imprime" (from the printer guide) → "Ya revisé el cable y está bien" was answered "¡Listo!".
    from backend.app.assistant.service import UnifiedAssistantService as S
    state = {"active_route": "knowledge", "active_document_id": "d", "history": [("La impresora no imprime", "…")]}
    for message in ("Ya revisé el cable y está bien", "Sigue igual", "Sigue sin imprimir", "No funciona",
                    "Tampoco funcionó", "Ya lo reinicié y nada"):
        assert S._continues_manual(state, message), message


class RawEvidenceKnowledge:
    """The printer guide: the first question has picked sentences; a follow-up the manual does not answer comes back
    as its raw first piece (no picked excerpts), which was shown as is."""

    def __init__(self):
        from test_named_reference import Store
        self.store, self.queries = Store(), []

    def procedure_catalog(self, document_id):
        return None

    async def query(self, request):
        import uuid as _uuid
        from backend.app.knowledge.models import KnowledgeAnswer
        self.queries.append(request)
        source = {"title": "network-printer-guide", "document_id": "doc-printer", "page": 1, "version": "1",
                  "revision": 1, "citation_id": "cit-printer"}
        first = len(self.queries) == 1
        return KnowledgeAnswer(
            status="answered", conversation_id=str(_uuid.uuid4()), intent="information", options=[], sources=[source],
            documentation_sufficient=True, retrieval={},
            answer="Según «network-printer-guide»: Verifica que la impresora esté encendida." if first else
                   "Según «network-printer-guide»:\n\nNetwork Printer Troubleshooting Guide Document ID: IT-PRN-004",
            excerpts=[{"text": "Check that the printer is turned on.", "page": 1}] if first else [])


def test_a_follow_up_the_manual_does_not_answer_is_answered_with_its_context():
    import uuid as _uuid
    from backend.app.assistant.service import UnifiedAssistantService

    class Provider:
        def __init__(self):
            self.prompts = []

        async def generate(self, message):
            self.prompts.append(message)
            return "Entonces revisa la cola de impresión y que el driver esté instalado."

    provider = Provider()
    service = UnifiedAssistantService(RawEvidenceKnowledge(), provider, web_search=StubWebSearch())
    sid = str(_uuid.uuid4())
    asyncio.run(service.chat("La impresora no imprime", session_id=sid))
    answer = asyncio.run(service.chat("Ya revisé el cable y está bien", session_id=sid))
    assert "Document ID" not in answer["answer"] and "Troubleshooting Guide" not in answer["answer"]
    assert "driver" in answer["answer"]
    prompt = provider.prompts[-1]
    assert "Justo antes, según un manual" in prompt and "La impresora no imprime" in prompt   # the thread
