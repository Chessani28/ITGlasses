import sqlite3
db_path = r'D:\ITGlasses\storage\knowledge\knowledge.sqlite3'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
# Get the Training Setup Guideline document chunks
cursor.execute('''SELECT c.id, c.text, d.title, d.scope, d.procedure_id, d.procedure_key
                  FROM chunks c 
                  JOIN documents d ON c.document_id=d.id AND d.current_revision=c.revision
                  WHERE d.id = ?
                  AND d.authorized=1 AND d.withdrawn=0''', ('fd237705-290a-464a-86c0-db959966c940',))
chunks = cursor.fetchall()
print('Training Setup Guideline chunks:')
for c in chunks:
    print(f'  Chunk ID: {c[0]}')
    print(f'  Text: {c[1][:200]}')
    print(f'  Title: {c[2]}, Scope: {c[3]}')
    print(f'  procedure_id: {c[4]}, procedure_key: {c[5]}')
    print()
conn.close()