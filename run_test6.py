import sys
sys.path.insert(0, r"D:\ITGlasses\backend")
from backend.app.main import create_app
from fastapi.testclient import TestClient

# Create the app
app = create_app()
client = TestClient(app)

# Test 1: Simple question via knowledge chat
print("Test 1: Simple question via /api/v1/knowledge/chat")
r = client.post("/api/v1/knowledge/chat", json={"message": "Hola"})
print(f"Status: {r.status_code}")
print(f"Response: {r.text[:500]}")

# Test 2: Web search query via knowledge chat
print("\nTest 2: Web search query via /api/v1/knowledge/chat")
r = client.post("/api/v1/knowledge/chat", json={"message": "Busca en internet la versión más reciente de Windows 11"})
print(f"Status: {r.status_code}")
print(f"Response: {r.text[:500]}")

# Test 3: What is VPN
print("\nTest 3: What is VPN via /api/v1/knowledge/chat")
r = client.post("/api/v1/knowledge/chat", json={"message": "¿Qué es una VPN?"})
print(f"Status: {r.status_code}")
print(f"Response: {r.text[:500]}")