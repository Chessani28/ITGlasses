with open(r'd:\ITGlasses\backend\app\knowledge\service.py', 'r', encoding='utf-8') as f:
    content = f.read()

idx = content.find('async def query')
# Print from the 'documents = self.store.documents' part to the retrieval part
section = content[idx:]
# Find the retrieval code
import re
# Look for the retriever search code
lines = section.split('\n')
for i, line in enumerate(lines[:200]):
    if 'retriever' in line.lower() or 'knowledge_chat' in line.lower() or 'respond' in line.lower() or 'ranker' in line.lower():
        # Print from this line backwards to get context
        start = max(0, i-5)
        print(f'Line {start}-{i}:')
        for j in range(start, min(i+1, len(lines))):
            print(f'{start+j+1}: {lines[j]}')
        print('---')
        break