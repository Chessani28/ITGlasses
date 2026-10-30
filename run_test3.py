import sys
sys.path.insert(0, r"D:\ITGlasses\backend")
from fastapi.testclient import TestClient
from backend.app.api.routes import app
client = TestClient(app)

# Test 1: Simple question
print("Test 1: Simple question")
r = client.post("/chat", json={"message": "Hola"})
print(f"Status: {r.status_code}")
print(f"Response: {r.text[:200]}")

# Test 2: Web search query
print("\nTest 2: Web search query")
r = client.post("/chat", json={"message": "Busca en internet la versión más reciente de Windows 11"})
print(f"Status: {r.status_code}")
print(f"Response: {r.text[:500]}")