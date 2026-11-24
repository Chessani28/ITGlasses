"""Stage 6: behind Caddy, the backend trusts X-Forwarded-* only from this PC, so a phone cannot fake its IP."""
import ast
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

RUN = Path(__file__).resolve().parents[1] / "backend" / "run.py"


def seen_by_backend(peer, headers):
    inner = FastAPI()

    @inner.get("/peer")
    def peer_route(request: Request):
        return {"client": request.client.host, "scheme": request.url.scheme}

    app = ProxyHeadersMiddleware(inner, trusted_hosts="127.0.0.1")
    with TestClient(app, client=(peer, 50000)) as client:
        return client.get("/peer", headers=headers).json()


def test_caddy_on_this_pc_passes_the_phone_address_and_https():
    assert seen_by_backend("127.0.0.1", {"X-Forwarded-For": "192.168.50.50", "X-Forwarded-Proto": "https"}) == \
        {"client": "192.168.50.50", "scheme": "https"}


def test_untrusted_peer_cannot_fake_its_address():
    assert seen_by_backend("192.168.50.99", {"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https"}) == \
        {"client": "192.168.50.99", "scheme": "http"}


def test_a_fake_entry_on_the_left_of_the_chain_does_not_win():
    assert seen_by_backend("127.0.0.1", {"X-Forwarded-For": "203.0.113.7, 192.168.50.50"})["client"] == "192.168.50.50"


def test_run_trusts_forwarded_headers_only_from_loopback():
    # Read run.py instead of executing it: importing it creates .cache/tmp and changes TEMP/TMP.
    calls = [node for node in ast.walk(ast.parse(RUN.read_text(encoding="utf-8")))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr == "run" and getattr(node.func.value, "id", None) == "uvicorn"]
    assert len(calls) == 1
    options = {keyword.arg: ast.literal_eval(keyword.value) for keyword in calls[0].keywords
               if keyword.arg in ("proxy_headers", "forwarded_allow_ips")}
    assert options == {"proxy_headers": True, "forwarded_allow_ips": "127.0.0.1"}
