"""Un manual con varias listas numeradas bajo encabezados distintos: cada lista es su propio procedimiento; la
numeración que vuelve a empezar nunca pisa los pasos de la lista anterior."""
from backend.app.assistant.procedures import parse_document

DIAGNOSE = "Pasos para diagnosticar y resolver problemas de VPN:"
PERSISTS = "Si el problema persiste:"
EMERGENCY = "Procedimiento de emergencia para VPN caído:"
CHUNKS = [
    {"id": "c1", "section": DIAGNOSE, "text": "\n".join([
        "1. Verificar que la conexión a internet esté activa y estable.",
        "2. Confirmar que el cliente VPN esté instalado y actualizado.",
        "3. Verificar las credenciales de usuario.",
        "4. Comprobar que el servidor VPN esté en línea y accesible.",
        "5. Revisar la configuración de red local (firewall, proxy, DNS)."])},
    {"id": "c2", "section": PERSISTS, "text": "\n".join([
        "1. Contactar al equipo de soporte técnico.",
        "2. Proporcionar el código de error mostrado por el cliente VPN.",
        "3. Incluir la hora exacta del intento de conexión.",
        "4. Mencionar el sistema operativo y versión del cliente VPN."])},
    {"id": "c3", "section": EMERGENCY, "text": "\n".join([
        "1. Activar conexión de respaldo (4G/5G o red alternativa).",
        "2. Notificar al equipo de TI sobre la caída del servicio."])},
]


def test_each_numbered_list_is_its_own_procedure():
    procedures = parse_document(CHUNKS)
    assert [p["title"] for p in procedures] == [DIAGNOSE.rstrip(":"), PERSISTS.rstrip(":"), EMERGENCY.rstrip(":")]
    assert [len(p["steps"]) for p in procedures] == [5, 4, 2]


def test_a_restarted_list_never_overwrites_the_first_one():
    first = parse_document(CHUNKS)[0]
    assert first["steps"][3]["text"] == "Comprobar que el servidor VPN esté en línea y accesible."
    assert not any("Mencionar" in step["text"] for step in first["steps"])


def test_generic_sections_do_not_become_titles():
    chunks = [{"id": "a", "section": "Document", "text": "Pasos: 1. Apagar el servidor. 2. Esperar 30 segundos."},
              {"id": "b", "section": "Page 3", "text": "1. Connect the cable.\n2. Turn it on."},
              {"id": "c", "section": "Pasos", "text": "1. Descargar el instalador.\n2. Ejecutar."}]
    titles = [p["title"] for p in parse_document(chunks)]
    assert not {"Document", "Page 3", "Pasos"} & set(titles)


def vpn_service():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from test_assistant_service import StubKnowledge, build
    procedures = parse_document(CHUNKS)
    for procedure in procedures:
        for step in procedure["steps"]:
            step["procedure"] = procedure["title"]
    catalog = {"document": {"id": "doc-1", "title": "Manual de Soporte VPN", "authorized": True},
               "procedures": procedures}
    sources = [{"title": "Manual de Soporte VPN", "document_id": "doc-1", "page": None, "version": "1",
                "revision": 1, "filename": "vpn.md", "citation_id": "cit-1"}]
    return build(knowledge=StubKnowledge(catalog=catalog, sources=sources))


def ask(service, message, session_id=None):
    import asyncio
    return asyncio.run(service.chat(message, session_id))


def test_the_list_that_clearly_matches_the_question_is_chosen():
    service = vpn_service()
    first = ask(service, "Dame los pasos para solucionar un problema de conexión VPN")
    assert first["answer"].startswith("«Pasos para diagnosticar y resolver problemas de VPN» (5 pasos)")
    third = ask(service, "Explícame el paso 3", first["session_id"])
    assert "credenciales" in third["answer"]
    following = ask(service, "¿Y el siguiente?", first["session_id"])
    assert "servidor" in following["answer"]


def test_an_ambiguous_question_still_asks_which_list():
    service = vpn_service()
    answer = ask(service, "Dame los pasos del manual de VPN")["answer"]
    assert answer.startswith("¿Qué procedimiento quieres consultar?")


def test_steps_continuing_in_the_next_chunk_of_the_same_list_stay_together():
    chunks = [{"id": "a", "section": DIAGNOSE, "text": "1. Uno.\n2. Dos."},
              {"id": "b", "section": DIAGNOSE, "text": "3. Tres.\n4. Cuatro."}]
    procedures = parse_document(chunks)
    assert len(procedures) == 1 and len(procedures[0]["steps"]) == 4
