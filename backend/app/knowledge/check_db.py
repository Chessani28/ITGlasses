import sqlite3
db_path = r'D:\ITGlasses\storage\knowledge\knowledge.sqlite3'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
# List all tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print('Tables:', tables)
# List all documents
cursor.execute("SELECT id, title, scope, authorized, withdrawn, current_revision FROM documents")
docs = cursor.fetchall()
print('Documents:')
for d in docs:
    print(f'  ID: {d[0]}, Title: {d[1]}, Scope: {d[2]}, Authorized: {d[3]}, Withdrawn: {d[4]}, Revision: {d[5]}')
conn.close()