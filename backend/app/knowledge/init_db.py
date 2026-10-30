import asyncio
import sys
import os
sys.path.insert(0, r'd:\ITGlasses')

from backend.app.knowledge.service import KnowledgeService
from backend.app.core.config import PROJECT_ROOT, settings
from backend.app.knowledge.embeddings import OllamaEmbeddings
from backend.app.knowledge.grounding import EvidenceSelector
from pathlib import Path

# Read the test fixture
fixture_path = r'd:\ITGlasses\tests\fixtures\knowledge\windows.md'
with open(fixture_path, 'r', encoding='utf-8') as f:
    raw = f.read()

print(f'Fixture content (first 200 chars): {raw[:200]}')

# Create knowledge service
embeddings = OllamaEmbeddings(settings.ollama_url)
selector = EvidenceSelector(settings.model, settings.ollama_url)

svc = KnowledgeService(PROJECT_ROOT, embeddings, selector)

# Try to ingest the document using the service's ingest method
# First, let's just check the store and try a direct approach
print(f'Store path: {svc.store.path}')
print(f'Store root exists: {svc.store.root.exists()}')

# List current documents
docs = svc.store.documents(eligible_only=True)
print(f'Current eligible documents: {len(docs)}')

# Check the chunks
chunks = svc.eligible_chunks()
print(f'Current eligible chunks: {len(chunks)}')