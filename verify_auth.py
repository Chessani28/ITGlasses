"""Verify authentication enforcement with ITGLASSES_REQUIRE_AUTH=1"""
import os

# Set auth required
os.environ["ITGLASSES_REQUIRE_AUTH"] = "1"

from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.providers.mock import MockLLM

print("=" * 60)
print("Testing auth enforcement with ITGLASSES_REQUIRE_AUTH=1")
print("=" * 60)

with TestClient(create_app(MockLLM())) as client:
    # Test 1: Chat without auth should get 401
    print("\n1. POST /api/v1/chat without auth header:")
    response = client.post("/api/v1/chat", json={"message": "hello"})
    print(f"   Status: {response.status_code}")
    assert response.status_code == 401, f"Expected 401, got {response.status_code}"
    print("   ✓ Correctly rejected (401)")
    
    # Test 2: Chat with invalid token should get 401
    print("\n2. POST /api/v1/chat with invalid token:")
    response = client.post("/api/v1/chat", json={"message": "hello"}, headers={"Authorization": "Bearer wrongtoken"})
    print(f"   Status: {response.status_code}")
    assert response.status_code == 401, f"Expected 401, got {response.status_code}"
    print("   ✓ Correctly rejected (401)")
    
    # Test 3: Health should still be public
    print("\n3. GET /health without auth:")
    response = client.get("/health")
    print(f"   Status: {response.status_code}")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    print("   ✓ Health still public (200)")
    print(f"   Body: {response.json()}")
    
    # Test 4: Status should still be public
    print("\n4. GET /api/v1/status without auth:")
    response = client.get("/api/v1/status")
    print(f"   Status: {response.status_code}")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    print("   ✓ Status still public (200)")
    print(f"   Body: {response.json()}")
    
    # Test 5: Assistant chat should require auth
    print("\n5. POST /api/v1/assistant/chat without auth:")
    response = client.post("/api/v1/assistant/chat", json={"message": "hello"})
    print(f"   Status: {response.status_code}")
    assert response.status_code == 401, f"Expected 401, got {response.status_code}"
    print("   ✓ Assistant chat correctly rejected (401)")
    
    # Test 6: Documents list should require auth
    print("\n6. GET /api/v1/documents without auth:")
    response = client.get("/api/v1/documents")
    print(f"   Status: {response.status_code}")
    assert response.status_code == 401, f"Expected 401, got {response.status_code}"
    print("   ✓ Documents correctly rejected (401)")
    
    # Test 7: Voice status should require auth
    print("\n7. GET /api/v1/voice/status without auth:")
    response = client.get("/api/v1/voice/status")
    print(f"   Status: {response.status_code}")
    assert response.status_code == 401, f"Expected 401, got {response.status_code}"
    print("   ✓ Voice status correctly rejected (401)")
    
    print("\n" + "=" * 60)
    print("All auth enforcement tests passed!")
    print("=" * 60)

# Clean up
del os.environ["ITGLASSES_REQUIRE_AUTH"]