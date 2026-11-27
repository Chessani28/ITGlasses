"""QR pairing link (2026-11-26): one APK learns the server's origin and certificate pin from the web panel."""
import base64
import datetime
import hashlib
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from backend.app.pairing import link
from test_device_pairing import ROOT, admin_client, setup  # noqa: F401  (setup is a fixture)

URL = ROOT + "/admin/pairing-link"


def certificate(name="ITGlasses prueba"):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
            .sign(key, hashes.SHA256()))
    spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    expected = base64.urlsafe_b64encode(hashlib.sha256(spki).digest()).rstrip(b"=").decode()
    return cert.public_bytes(serialization.Encoding.DER), expected


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setattr(link, "_cache", {})
    monkeypatch.delenv("ITGLASSES_PAIRING_PIN", raising=False)


def test_the_pin_is_the_sha256_of_the_public_key():
    der, expected = certificate()
    assert link.spki_pin(der) == expected
    assert len(expected) == 43


def test_the_top_certificate_of_the_chain_is_pinned_and_cached():
    leaf, _ = certificate("servidor")
    ca, ca_pin = certificate("CA piloto")
    calls = []

    def fetch(origin):
        calls.append(origin)
        return [leaf, ca]

    assert link.server_pin("https://10.0.0.5:8443", now=100, fetch=fetch) == ca_pin
    assert link.server_pin("https://10.0.0.5:8443", now=200, fetch=fetch) == ca_pin
    assert len(calls) == 1                                                   # cached
    link.server_pin("https://10.0.0.5:8443", now=100 + 601, fetch=fetch)
    assert len(calls) == 2                                                   # expired: read again


def test_a_configured_pin_wins_and_must_look_like_one(monkeypatch):
    monkeypatch.setenv("ITGLASSES_PAIRING_PIN", "A" * 43)
    assert link.server_pin("https://10.0.0.5:8443", fetch=lambda _: pytest.fail("no TLS read")) == "A" * 43
    monkeypatch.setenv("ITGLASSES_PAIRING_PIN", "corto")
    with pytest.raises(link.Unavailable):
        link.server_pin("https://10.0.0.5:8443")


def test_the_link_carries_version_origin_and_pin():
    text = link.link("https://172.16.5.142:8443", "B" * 43)
    assert text.startswith("itglasses://emparejar?")
    query = parse_qs(urlsplit(text).query)
    assert query == {"v": ["1"], "o": ["https://172.16.5.142:8443"], "p": ["B" * 43]}


@pytest.mark.parametrize("origin, ok", [("https://172.16.5.142:8443", True), ("https://itglasses.jatco.local", True),
                                        ("http://172.16.5.142:8443", False), ("https://x.com/ruta", False),
                                        ("https://X.com", False), ("", False)])
def test_only_a_canonical_https_origin(origin, ok):
    assert link.valid_origin(origin) is ok


def test_an_unreachable_server_is_unavailable():
    with pytest.raises(link.Unavailable):
        link.presented_chain("https://127.0.0.1:9", timeout=0.5)


def test_the_qr_is_an_svg():
    svg = link.qr_svg(link.link("https://172.16.5.142:8443", "C" * 43))
    assert svg.startswith("<svg") and "</svg>" in svg


def test_the_admin_route_returns_link_and_qr(setup, monkeypatch):
    c, _, _ = setup
    _, pin = certificate()
    monkeypatch.setenv("ITGLASSES_PAIRING_PIN", pin)
    response = admin_client(c).get(URL)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["pin"] == pin and body["link"] == link.link(body["origin"], pin)
    assert body["svg"].startswith("<svg")


def test_the_admin_route_without_origin_or_certificate(setup, monkeypatch):
    c, _, _ = setup
    monkeypatch.setattr(link, "presented_chain", lambda origin: (_ for _ in ()).throw(link.Unavailable()))
    assert admin_client(c).get(URL).status_code == 503
    monkeypatch.delenv("ITGLASSES_PAIRING_ORIGIN")
    assert admin_client(c).get(URL).status_code == 404


def test_the_route_is_admin_only(setup):
    c, _, _ = setup
    assert admin_client(c, address="192.168.50.20").get(URL).status_code == 403
