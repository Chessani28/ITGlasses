import base64
import hashlib
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

from backend.app.main import create_app
from backend.app.providers.mock import MockLLM

ORIGIN = "https://pilot.example"
ROOT = "/api/v1"


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Phone:
    def __init__(self, client):
        self.client = client
        self.key = ec.generate_private_key(ec.SECP256R1())

    def headers(self, method, path, token=None, **claims):
        point = self.key.public_key().public_numbers()
        jwk = dict(kty="EC", crv="P-256", x=b64(point.x.to_bytes(32)), y=b64(point.y.to_bytes(32)))
        header = dict(typ="dpop+jwt", alg="ES256", jwk=jwk)
        data = dict(htm=method, htu=ORIGIN + ROOT + path, iat=int(time.time()), jti=uuid.uuid4().hex, **claims)
        headers = {}
        if token:
            data["ath"] = b64(hashlib.sha256(token.encode()).digest())
            headers["Authorization"] = "DPoP " + token
        signed = ".".join(b64(json.dumps(it).encode()) for it in (header, data))
        r, s = utils.decode_dss_signature(self.key.sign(signed.encode(), ec.ECDSA(hashes.SHA256())))
        headers["DPoP"] = signed + "." + b64(r.to_bytes(32) + s.to_bytes(32))
        return headers

    def post(self, path, **claims):
        return self.client.post(ROOT + path, headers=self.headers("POST", path, **claims))


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from backend.app.pairing.store import provision
    db = str(tmp_path / "pair.sqlite")
    monkeypatch.setenv("ITGLASSES_PAIRING_DB", db)
    monkeypatch.setenv("ITGLASSES_PAIRING_ORIGIN", ORIGIN)
    provision(db, "only-for-this-test")
    with TestClient(create_app(MockLLM()), base_url=ORIGIN) as c:
        yield c, Phone(c), Phone(c)


def test_lifecycle(setup):
    c, a, b = setup
    def admin(path, **body):
        return c.post(ROOT + "/admin/" + path, json=body, headers={"Origin": ORIGIN, "X-ITGlasses-Admin":"1"})
    req = a.post("/pairing/requests", variant="phone").json()
    rid = req["request_id"]
    other = b.post("/pairing/requests", variant="meta").json()
    assert other["user_code"] != req["user_code"]
    assert a.post("/pairing/requests", variant="phone").json()["request_id"] == rid
    assert a.post("/pairing/claim", rid=rid).status_code == 409
    assert c.get(ROOT + "/admin/devices", headers={"Authorization":"Bearer enterprise"}).status_code == 401
    assert admin("login", password="only-for-this-test").status_code == 200
    assert c.post(ROOT + "/admin/decision", json={"id":rid,"decision":"approve"}).status_code == 403
    assert admin("decision", id=other["request_id"], decision="deny").status_code == 200
    assert b.post("/pairing/claim", rid=other["request_id"]).status_code == 409
    assert admin("decision", id=rid, decision="approve").status_code == 200
    assert b.post("/pairing/claim", rid=rid).status_code == 401
    token = a.post("/pairing/claim", rid=rid).json()["access_token"]
    path = "/chat"
    def chat(headers):
        return c.post(ROOT + path, headers=headers, json={"message":"hola"})
    signed = a.headers("POST", path, token)
    assert chat(signed).status_code == 200
    assert chat(signed).status_code == 401
    assert chat(b.headers("POST", path, token)).status_code == 401
    assert chat({}).status_code == 401
    assert c.get(ROOT + "/documents", headers=a.headers("GET","/documents",token)).status_code == 403
    nonce = a.post("/devices/challenge", rid=rid).json()["nonce"]
    renewed = a.post("/devices/token", rid=rid, nonce=nonce).json()["access_token"]
    assert a.post("/devices/token", rid=rid, nonce=nonce).status_code == 401
    assert admin("revoke", id=rid).status_code == 200
    assert chat(a.headers("POST", path, renewed)).status_code == 401
    assert a.post("/devices/challenge", rid=rid).status_code == 401


def test_transport_and_legacy(setup, monkeypatch):
    c, a, _ = setup
    assert c.post("http://pilot.example" + ROOT + "/pairing/requests", headers=a.headers("POST","/pairing/requests",variant="phone")).status_code == 403
    monkeypatch.setenv("ITGLASSES_API_TOKENS","existing-test-token")
    assert c.post(ROOT+"/chat",json={"message":"hola"},headers={"Authorization":"Bearer existing-test-token"}).status_code == 200
    monkeypatch.delenv("ITGLASSES_PAIRING_ORIGIN")
    assert a.post("/pairing/requests", variant="phone").status_code == 404

