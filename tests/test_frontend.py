"""UI hosting and browser-facing contracts; synthetic dependencies only."""
import json
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.providers.mock import MockLLM
from test_knowledge import service


def test_frontend_assets_and_security_headers(tmp_path):
    with TestClient(create_app(MockLLM(), service(tmp_path))) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert '<html lang="es">' in response.text
        assert "unsafe-inline" not in response.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"
        for name in ["styles.css", "app.js", "core.mjs", "favicon.svg"]:
            asset = client.get("/ui/" + name)
            assert asset.status_code == 200
            assert asset.headers["cache-control"] == "no-store"
        assert "javascript" in client.get("/ui/core.mjs").headers["content-type"]


def test_frontend_does_not_expose_project_files(tmp_path):
    with TestClient(create_app(MockLLM(), service(tmp_path))) as client:
        for path in ["/ui/.env", "/ui/config.toml", "/ui/%2e%2e/config.toml",
                     "/documents/private.txt", "/storage/knowledge/knowledge.sqlite3"]:
            assert client.get(path).status_code == 404


def test_browser_modes_and_document_lifecycle(tmp_path):
    with TestClient(create_app(MockLLM(), service(tmp_path)),
                    base_url="http://127.0.0.1:8000",
                    headers={"Origin": "http://127.0.0.1:8000"}) as client:
        general = client.post("/api/v1/chat", json={"message": "Explain DNS."})
        assert general.status_code == 200 and general.json()["provider"] == "mock"
        assert general.json()["sources"] == []
        docs = []
        for title, content in [("Procedimiento 24 Windows", b"Formatear Windows."), ("Printer", b"Replace printer toner.")]:
            uploaded = client.post("/api/v1/documents",
                files={"file": ("synthetic.txt", content)},
                data={"metadata": json.dumps({"title": title, "procedure_id": "24", "authorized": True})})
            assert uploaded.status_code == 201
            docs.append(uploaded.json())
        answer = client.post("/api/v1/knowledge/chat", json={"message": "procedimiento 24"}).json()
        assert answer["status"] == "clarification_required"
        clarified = client.post("/api/v1/knowledge/chat", json={
            "message": "La opción seleccionada.", "conversation_id": answer["conversation_id"],
            "choice_document_id": docs[0]["id"]}).json()
        assert clarified["status"] == "answered" and clarified["intent"] == "identify"
        # Never an internal id in the text (2026-11-24: "[68b5179f-…]" was shown); the sources carry them.
        assert clarified["sources"][0]["citation_id"] not in clarified["answer"] and "[" not in clarified["answer"]
        assert clarified["sources"][0]["quote"] == ""
        source = clarified["sources"][0]["url"]
        assert client.get(source).json()["quote"] == "Formatear Windows."
        doc_id = docs[0]["id"]
        assert client.patch(f"/api/v1/documents/{doc_id}/authorization", json={"authorized": False}).status_code == 200
        assert client.get(source).status_code == 404
        assert client.patch(f"/api/v1/documents/{doc_id}/authorization", json={"authorized": True}).status_code == 200
        assert client.post(f"/api/v1/documents/{doc_id}/reindex").status_code == 200
        assert client.post(f"/api/v1/documents/{doc_id}/withdraw").json()["withdrawn"] == 1
        assert client.get("/api/v1/knowledge/status").json()["eligible_documents"] == 1


def test_browser_invalid_upload_has_sanitized_error(tmp_path):
    with TestClient(create_app(MockLLM(), service(tmp_path))) as client:
        response = client.post("/api/v1/documents", files={"file": ("x.exe", b"private content")},
                               data={"metadata": '{"title":"Invalid"}'})
        assert response.status_code == 400 and "error" in response.json()
        assert "private content" not in response.text
