import asyncio
import sys
sys.path.insert(0, r'd:\ITGlasses')

from backend.app.knowledge.service import KnowledgeService
from backend.app.core.config import PROJECT_ROOT, settings
from backend.app.knowledge.embeddings import OllamaEmbeddings
from backend.app.knowledge.grounding import EvidenceSelector
from backend.app.knowledge.retrieval import Retriever
from backend.app.knowledge.store import Store
from backend.app.assistant.service import _strip_spanish_prefix, match_procedures
from pathlib import Path

# Create knowledge service
embeddings = OllamaEmbeddings(settings.ollama_url)
selector = EvidenceSelector(settings.model, settings.ollama_url)
svc = KnowledgeService(PROJECT_ROOT, embeddings, selector)

# Test the retriever directly
question = '¿Cuáles son los 8 pasos de Computer Preparation según Training Setup Guideline?'

# Get eligible rows
rows = svc.store.eligible_chunks()
print(f'Eligible chunks: {len(rows)}')

# Test _strip_spanish_prefix
stripped = _strip_spanish_prefix(question)
print(f'Original: {question}')
print(f'Stripped: {stripped}')

# Test match_procedures
procedures = [{'title': 'Training Setup Guideline', 'steps': ['step1', 'step2', 'step3', 'step4', 'step5', 'step6', 'step7', 'step8']}]
matches = match_procedures(procedures, question)
print(f'\nmatch_procedures with original question: {len(matches)} matches')
if matches:
    print(f'Title: {matches[0]["title"]}')

matches = match_procedures(procedures, stripped)
print(f'match_procedures with stripped question: {len(matches)} matches')
if matches:
    print(f'Title: {matches[0]["title"]}')