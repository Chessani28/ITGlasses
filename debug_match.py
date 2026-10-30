import re
TOKEN = re.compile(r"[a-z0-9]+")
def _tokens(text):
    return set(TOKEN.findall((text or "").casefold()))

def match_procedures(procedures, message):
    """Return the procedures whose title is referenced by ``message``."""
    message_tokens = _tokens(message)
    matches = []
    for procedure in procedures or []:
        title_tokens = _tokens(procedure.get("title"))
        if not title_tokens:
            continue
        if title_tokens <= message_tokens:
            matches.append(procedure)
    matches.sort(key=lambda p: len(_tokens(p.get("title"))), reverse=True)
    return matches

# Test
from tests.test_assistant_service import CATALOG, build, StubStepsKnowledge

service = build(knowledge=StubStepsKnowledge(catalog=CATALOG))

# Simulate what happens after knowledge query and catalog load
# _load_catalog does: procedures = catalog.get("procedures") or []
catalog = service.knowledge.procedure_catalog("doc-1")
procedures = catalog.get("procedures") or []
print('Procedures from catalog:', procedures)
print('Type:', type(procedures))
print('Number of procedures:', len(procedures))

msg = '¿Cuáles son los 8 pasos de Computer Preparation?'
matched = match_procedures(procedures, msg)
print('Matched procedures:', matched)

if matched:
    for p in matched:
        steps = p.get("steps") or []
        print(f'Matched procedure: {p.get("title")}, steps count: {len(steps)}')
        for i, s in enumerate(steps):
            print(f'  Step {i+1}: {s.get("text")}')