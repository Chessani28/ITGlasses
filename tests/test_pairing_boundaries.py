import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from test_device_pairing import ORIGIN, ROOT, setup
from test_voice import FakeVoice, wav

ADMIN = {"Origin":ORIGIN,"X-ITGlasses-Admin":"1"}


def authorize(c, phone):
    reg = phone.post("/pairing/requests",variant="phone").json()
    rid = reg["request_id"]
    assert c.post(ROOT+"/admin/login",json={"password":"only-for-this-test"},headers=ADMIN).is_success
    assert c.post(ROOT+"/admin/decision",json={"id":rid,"decision":"approve"},headers=ADMIN).is_success
    return rid


def test_two_active_devices_are_independent(setup):
    c, a, b = setup
    rid_a = authorize(c,a)
    rid_b = authorize(c,b)
    token_a = a.post("/pairing/claim",rid=rid_a).json()["access_token"]
    token_b = b.post("/pairing/claim",rid=rid_b).json()["access_token"]
    path = "/assistant/chat"
    for phone, token in ((a,token_a),(b,token_b)):
        response = c.post(ROOT+path, headers=phone.headers("POST",path,token),
                          json={"message":"hola", "session_id":"shared"})
        assert response.status_code == 200 and response.json()["session_id"] == "shared"
    sessions = c.app.state.assistant.sessions._sessions
    assert sessions[(rid_a,"shared")] is not sessions[(rid_b,"shared")]
    assert c.post(ROOT+"/admin/revoke",json={"id":rid_a},headers=ADMIN).is_success
    path = "/devices/me"
    assert c.get(ROOT+path,headers=a.headers("GET",path,token_a)).status_code == 401
    assert c.get(ROOT+path,headers=b.headers("GET",path,token_b)).json()["device_id"] == rid_b
    assert c.get(ROOT+path,headers=b.headers("GET","/chat",token_b)).status_code == 401
    assert c.get(ROOT+path,headers=b.headers("POST",path,token_b)).status_code == 401
    assert c.post(ROOT+"/chat",json={"message":"hola"},headers={"Authorization":"Bearer "+token_b}).status_code == 401


def test_admin_session_and_csrf(setup):
    c, _, _ = setup
    url = ROOT+"/admin/login"
    assert c.post(url,json={"password":"only-for-this-test"},headers={**ADMIN,"Origin":"https://attacker.example"}).status_code == 403
    reply = c.post(url,json={"password":"only-for-this-test"},headers=ADMIN)
    cookie = reply.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert c.get(ROOT+"/documents").status_code == 401
    assert c.post(ROOT+"/admin/logout",json={},headers=ADMIN).is_success
    assert c.get(ROOT+"/admin/devices").status_code == 401
    for _ in range(4):
        assert c.post(url,json={"password":"incorrect"},headers=ADMIN).status_code == 401
    assert c.post(url,json={"password":"only-for-this-test"},headers=ADMIN).status_code == 429


def test_concurrent_claim_and_lost_response(setup):
    c, phone, _ = setup
    rid = authorize(c,phone)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _:phone.post("/pairing/claim",rid=rid).status_code,range(2)))
    assert sorted(results) == [200,409]
    assert phone.post("/pairing/status",rid=rid).json()["state"] == "active"
    assert phone.post("/pairing/requests",variant="phone").json()["request_id"] == rid


def test_expiry_and_request_limits(setup):
    c, phone, _ = setup
    rid = authorize(c,phone)
    with sqlite3.connect(os.environ["ITGLASSES_PAIRING_DB"]) as db:
        db.execute("UPDATE installs SET expires=0 WHERE id=?", (rid,))
    assert phone.post("/pairing/status",rid=rid).json()["state"] == "expired"
    assert phone.post("/pairing/claim",rid=rid).status_code == 409
    for _ in range(14):
        assert phone.post("/pairing/requests",variant="phone").is_success
    blocked = phone.post("/pairing/requests",variant="phone")
    assert blocked.status_code == 429 and blocked.headers["retry-after"]


def test_mobile_voice_and_scope(setup):
    c, phone, _ = setup
    rid = authorize(c,phone)
    token = phone.post("/pairing/claim",rid=rid).json()["access_token"]
    c.app.state.voice = FakeVoice()
    for engine in ("vosk","whisper-small"):
        path = "/voice/transcribe"
        headers = {**phone.headers("POST",path,token),"Content-Type":"audio/wav","X-ITGlasses-STT-Engine":engine}
        assert c.post(ROOT+path,content=wav(),headers=headers).json()["engine"] == engine
    path = "/voice/synthesize"
    assert c.post(ROOT+path,json={"text":"hola"},headers=phone.headers("POST",path,token)).content.startswith(b"RIFF")
    for method,path in [("GET","/voice/diagnostic/settings"),("GET","/knowledge/status"),("GET","/admin/devices")]:
        c.cookies.clear()
        assert c.request(method,ROOT+path,headers=phone.headers(method,path,token)).status_code in (401,403)
    path = "/devices/me"
    with sqlite3.connect(os.environ["ITGLASSES_PAIRING_DB"]) as db:
        db.execute("UPDATE installs SET token_exp=0 WHERE id=?", (rid,))
    assert c.get(ROOT+path,headers=phone.headers("GET",path,token)).status_code == 401


def test_browser_voice_canonical_origin(setup, monkeypatch):
    c, _, _ = setup
    c.app.state.voice = FakeVoice()
    monkeypatch.setenv("ITGLASSES_API_TOKENS","legacy-test")
    headers = {"Origin":ORIGIN,"Authorization":"Bearer legacy-test"}
    assert c.get(ROOT+"/voice/status",headers=headers).is_success
    assert c.get(ROOT+"/voice/status",headers={**headers, "Origin":"https://other.example"}).status_code == 403


def test_rejected_proof_cannot_be_replayed_after_approval(setup):
    c, phone, _ = setup
    rid = phone.post("/pairing/requests",variant="phone").json()["request_id"]
    path = "/pairing/claim"
    signed = phone.headers("POST",path,rid=rid)
    assert c.post(ROOT+path,headers=signed).status_code == 409
    assert authorize(c,phone) == rid
    assert c.post(ROOT+path,headers=signed).status_code == 401

