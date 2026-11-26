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


# --- 6. Pending of 2026-11-26: names on screen protected during the translation ---------------------------------------

class PlaceholderProvider:
    """Translates the printer sentences keeping whatever placeholder it receives (as qwen does with "@@1@@")."""

    def __init__(self):
        self.prompts = []

    async def generate(self, message):
        self.prompts.append(message)
        text = message.rsplit("\n", 1)[-1]
        if "@@1@@" in text and "configuration page" in text:
            return "Imprime una página de configuración desde el menú de la impresora (@@1@@) y confirma la dirección IP."
        if "configuration page" in text:     # without the placeholder: renames the menu, like the real model
            return ("Imprime una página de configuración desde el menú de la impresora (Menú Sistema > Informe > "
                    "Página de Estado) y confirma la dirección IP.")
        return text


def test_a_menu_path_is_kept_exactly_and_the_sentence_is_translated():
    import uuid as _uuid
    from backend.app.assistant.service import UnifiedAssistantService
    from test_named_reference import Knowledge
    provider = PlaceholderProvider()
    service = UnifiedAssistantService(Knowledge(), provider, web_search=StubWebSearch())
    state = {}
    source = ("Print a configuration page from the printer menu (System Menu > Report > Status Page) and confirm the "
              "IP address.")
    spanish = asyncio.run(service._translate(state, source, ""))
    assert spanish == ("Imprime una página de configuración desde el menú de la impresora (System Menu > Report > "
                       "Status Page) y confirma la dirección IP.")
    assert "@@1@@" in provider.prompts[0] and "System Menu" not in provider.prompts[0]


def test_protected_names_cover_menu_paths_screen_names_and_quoted_labels():
    from backend.app.assistant.service import UnifiedAssistantService as S
    text, names = S._protect_names('Restart the Print Spooler service and open the "Recording" folder in Newly Added.')
    assert set(names.values()) == {"Print Spooler", '"Recording"', "Newly Added"}
    assert "Print Spooler" not in text and "@@1@@" in text
    assert S._protect_names("Turn the printer off and wait 10 minutes.") == ("Turn the printer off and wait 10 minutes.", {})


# --- 7. Pending of 2026-11-26: a section by its number -----------------------------------------------------------------

def test_a_section_asked_by_its_number_in_a_named_manual():
    # "En el manual de alta de laptops, ¿cuáles son los pasos del procedimiento 2?" answered "no identifiqué esa
    # sección" although the manual numbers its sections.
    import uuid as _uuid
    from test_named_reference import Knowledge, build
    from test_user_test_2026_11_26 import ask
    service, sid = build(Knowledge()), str(_uuid.uuid4())
    answer = ask(service, "En el Training Setup Guideline, ¿cuáles son los pasos de la sección 2?", sid)["answer"]
    assert answer.startswith("«WINDOWS DEPLOYMENT PROCEDURE» ("), answer[:120]


def test_a_section_number_of_the_manual_being_discussed():
    import uuid as _uuid
    from test_named_reference import Knowledge, build
    from test_user_test_2026_11_26 import ask
    service, sid = build(Knowledge()), str(_uuid.uuid4())
    ask(service, "¿Cuáles son los pasos de COMPUTER PREPARATION?", sid)
    answer = ask(service, "¿Y los pasos del procedimiento 2?", sid)["answer"]
    assert answer.startswith("«WINDOWS DEPLOYMENT PROCEDURE» ("), answer[:120]


def test_a_section_number_that_does_not_exist_is_not_invented():
    import uuid as _uuid
    from test_named_reference import Knowledge, build
    from test_user_test_2026_11_26 import ask
    service, sid = build(Knowledge()), str(_uuid.uuid4())
    answer = ask(service, "En el Training Setup Guideline, ¿cuáles son los pasos de la sección 9?", sid)["answer"]
    assert "WINDOWS DEPLOYMENT PROCEDURE» (" not in answer


def test_a_document_id_wins_over_a_section_number():
    from backend.app.assistant.service import UnifiedAssistantService
    from test_named_reference import Knowledge, StubProvider
    service = UnifiedAssistantService(Knowledge(), StubProvider(), web_search=StubWebSearch())
    ids = {d.get("procedure_id") for d in service.knowledge.store.documents(eligible_only=True)} - {None}
    for number in ids:
        assert service._is_document_id(number)
    assert not service._is_document_id("77")


def test_the_manual_the_question_names_wins_even_without_a_tie(tmp_path):
    # "¿Cuánta memoria RAM… para el training?" was answered from the laptops manual once its title changed.
    svc = KnowledgeService(tmp_path, EnglishOnlyEmbeddings(), TranslatingSelector("Which paper for the training?"))
    asyncio.run(svc.ingest("a.md", b"Load paper paper paper in tray 1 for the delivery letters.",
                           Metadata(title="Manual de Alta de Laptops", version="1", authorized=True)))
    asyncio.run(svc.ingest("b.md", b"Use letter paper in tray 1 for the training printouts.",
                           Metadata(title="Training Setup Guideline", version="1", authorized=True)))
    answer = ask(svc, "¿Qué papel uso para el training?")
    assert answer.status == "answered" and answer.sources[0]["title"] == "Training Setup Guideline"
    generic = ask(svc, "¿Qué papel uso según el manual?")                 # "manual" names no manual in particular
    assert generic.status in ("answered", "clarification_required")


class NoPickSelector(TranslatingSelector):
    async def pick_sentences(self, question, candidates):
        return None


def test_without_a_picked_sentence_the_closest_sentences_are_shown_not_the_manual_start(tmp_path):
    text = ("Network Printer Troubleshooting Guide\nDocument ID: IT-PRN-004 Version: 1.3\n"
            "Every printer is installed through the print server PRINT01.\n"
            "Users print to the shared queues named PRN-FLOOR-LETTER, for example PRN-3-A is printer A on the third floor.\n"
            "Toner cartridges are kept in the IT storeroom.\n")
    svc = KnowledgeService(tmp_path, EnglishOnlyEmbeddings(), NoPickSelector("What is the queue of printer A on the third floor?"))
    asyncio.run(svc.ingest("g.md", text.encode(), Metadata(title="Network Printer Troubleshooting Guide",
                                                             version="1", authorized=True)))
    answer = ask(svc, "¿Cómo se llama la cola de la impresora PRN-3-A del tercer piso?")
    assert answer.status == "answered"
    assert "PRN-3-A" in answer.answer and "Document ID" not in answer.answer


class ClosestEvidenceKnowledge(RawEvidenceKnowledge):
    """As RawEvidenceKnowledge, but the follow-up comes back with the closest sentences (no pick): the first step again."""

    async def query(self, request):
        answer = await super().query(request)
        if len(self.queries) == 1:
            return answer
        return answer.model_copy(update={"answer": "Según «network-printer-guide»: Check that the printer is turned on.",
                                         "excerpts": [{"text": "Check that the printer is turned on.", "page": 1}],
                                         "retrieval": {"sentences": "closest"}})


def test_closest_sentences_do_not_answer_a_follow_up():
    # "Ya revisé el cable y está bien" repeated "Verifica que la impresora esté encendida" (the closest sentence).
    import uuid as _uuid
    from backend.app.assistant.service import UnifiedAssistantService

    class Provider:
        def __init__(self):
            self.prompts = []

        async def generate(self, message):
            self.prompts.append(message)
            if message.startswith("Traduce"):
                return "Verifica que la impresora esté encendida."
            return "Entonces revisa el driver y la cola de impresión."

    provider = Provider()
    service = UnifiedAssistantService(ClosestEvidenceKnowledge(), provider, web_search=StubWebSearch())
    sid = str(_uuid.uuid4())
    asyncio.run(service.chat("La impresora no imprime", session_id=sid))
    answer = asyncio.run(service.chat("Ya revisé el cable y está bien", session_id=sid))
    assert "driver" in answer["answer"] and "encendida" not in answer["answer"]


# --- 8. Real manual cases (Training Setup Guideline) ---------------------------------------------------------------

def test_a_dotted_command_also_counts_its_parts():
    from backend.app.knowledge.retrieval import tokens
    assert {"secpol", "msc", "secpol.msc"} <= set(tokens("type in secpol.msc and hit Enter"))
    assert "2.20" in tokens("higher than 2.20 GHz") and "20" not in tokens("higher than 2.20 GHz")


SECPOL = """For Dominos CS
Before you start, make sure your computer is clean and updated.
Install Google Chrome by double-clicking ChromeSetup.
Access to this PC and open Virtual Observer.
Open RUN, by pressing Windows + R and type in secpol.msc.
"""
SECPOL_NEXT = """Once you open the Local Security Policy, click on Local Policies and then go to Security Options.
Right-click on User Account Control, select Virtualize File and Registry Write Failures, and then choose Disable.
"""


class FirstPickSelector(TranslatingSelector):
    """Picks the sentence of the candidates that contains a word, like the model would."""

    def __init__(self, translation, word):
        super().__init__(translation)
        self.word, self.candidates = word, []

    async def pick_sentences(self, question, candidates):
        self.candidates = candidates
        return [i for i, item in enumerate(candidates) if self.word in item["text"]][:1] or None


def test_a_rare_word_finds_its_piece_and_the_next_one(tmp_path):
    # "¿Qué cambio hago en secpol para Dominos?" found nothing: "secpol.msc" was one word, and the change is in the
    # next piece ("Virtualize… Disable").
    selector = FirstPickSelector("What change do I make in secpol for Dominos?", "Virtualize")
    svc = KnowledgeService(tmp_path, EnglishOnlyEmbeddings(), selector)
    text = ("Filler line about other apps and settings for the training setup.\n" * 8) + SECPOL + SECPOL_NEXT
    asyncio.run(svc.ingest("t.md", text.encode(), Metadata(title="Training Setup Guideline", version="1",
                                                           authorized=True)))
    answer = ask(svc, "¿Qué cambio hago en secpol para Dominos?")
    assert answer.status == "answered", answer.answer
    assert any("Virtualize" in item["text"] for item in selector.candidates)


def test_a_clear_word_margin_breaks_a_tie():
    from backend.app.knowledge.service import tie_by_words
    best = {"document_id": "laptops", "overlap": 1}
    training = {"document_id": "training", "overlap": 3}
    assert tie_by_words(best, [training]) == (training, [])
    close = {"document_id": "training", "overlap": 2}
    assert tie_by_words(best, [close]) == (best, [close])


def test_only_command_or_file_names_are_rare_words():
    from backend.app.knowledge.retrieval import rare_tokens
    texts = ["open run and type in secpol.msc", "the printer shows an error", "another error page", "clean the fan"]
    sets = [set(t.split()) | ({"secpol", "msc"} if "secpol" in t else set()) for t in texts]
    assert rare_tokens({"secpol", "error", "printer"}, sets, texts) == {"secpol"}


def test_a_question_about_a_rule_asks_the_manuals_first():
    # "¿Qué etiquetas se permiten pegar en los equipos?" was answered in general; the manual says only hostname labels.
    import uuid as _uuid
    from test_history_context import NoEvidence
    from test_named_reference import StubProvider
    from backend.app.assistant.service import UnifiedAssistantService
    for question in ("¿Qué etiquetas se permiten pegar en los equipos?", "¿Está prohibido instalar programas?",
                     "¿Es obligatorio limpiar el equipo antes de entregarlo?"):
        knowledge = NoEvidence()
        service = UnifiedAssistantService(knowledge, StubProvider(), web_search=StubWebSearch())
        asyncio.run(service.chat(question, session_id=str(_uuid.uuid4())))
        assert knowledge.queries, question


def test_a_failed_translation_is_retried_once():
    # "¿Qué etiquetas se permiten pegar en los equipos?" was searched without its English version (it came back
    # None once): the Spanish question does not match the English manual.
    import json as _json
    import httpx
    from backend.app.knowledge.grounding import EvidenceSelector
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(200, json={"done": True, "message": {"content": "no es json"}})
        return httpx.Response(200, json={"done": True, "message": {"content": _json.dumps(
            {"english": "What labels are allowed to be attached to the equipment?"})}})
    selector = EvidenceSelector("qwen-test", transport=httpx.MockTransport(handler))
    english = asyncio.run(selector.translate_query("¿Qué etiquetas se permiten pegar en los equipos?"))
    assert english == "What labels are allowed to be attached to the equipment?" and len(calls) == 2


def test_the_sentence_picker_gets_the_english_question():
    # "Para Dominos CS, ¿qué es lo primero que instalo?" picked "Before you start, make sure…" instead of
    # "Install Google Chrome by double-clicking ChromeSetup".
    import json as _json
    import httpx
    from backend.app.knowledge.grounding import EvidenceSelector
    seen = []

    def handler(request):
        seen.append(_json.loads(request.content))
        return httpx.Response(200, json={"done": True, "message": {"content": _json.dumps({"ids": ["1"]})}})
    selector = EvidenceSelector("qwen-test", transport=httpx.MockTransport(handler))
    sentences = [{"id": 0, "text": "Before you start, make sure your computer is clean."},
                 {"id": 1, "text": "Install Google Chrome by double-clicking ChromeSetup."}]
    picked = asyncio.run(selector.pick_sentences("Para Dominos CS, ¿qué es lo primero que instalo?", sentences,
                                                 question_en="For Dominos CS, what do I install first?"))
    assert picked == [1]
    payload = seen[0]
    asked = _json.loads(payload["messages"][1]["content"])
    assert asked["question_en"] == "For Dominos CS, what do I install first?"
    assert "question_en" in payload["messages"][0]["content"]


def test_a_picked_heading_or_question_brings_the_sentence_after_it():
    # The picker chose "5.2 For SUMMIT (All LOB's):" and "¿A quién se reporta un equipo que llega dañado?" alone.
    from backend.app.knowledge.service import with_following_sentence
    candidates = [{"text": "5.2 For SUMMIT (All LOB's):"}, {"text": "Install VC_redist x86 first."},
                  {"text": "¿A quién se reporta un equipo que llega dañado?"}, {"text": "A Compras, en 48 horas."},
                  {"text": "Clean the fan."}]
    assert with_following_sentence([0], candidates) == [0, 1]
    assert with_following_sentence([2], candidates) == [2, 3]
    assert with_following_sentence([4], candidates) == [4]
    assert with_following_sentence([1, 4], candidates) == [1, 4]
