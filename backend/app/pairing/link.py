"""Pairing link for the QR shown in the web panel (2026-11-26).

One APK for every customer: the phone learns which server to talk to by scanning
    itglasses://emparejar?v=1&o=<https origin>&p=<pin>
where the pin is the base64url SHA-256 of the public key (SPKI) of the top certificate the server presents
(the pilot CA when Caddy sends it, else the server certificate). The link holds nothing secret: the admin still
approves each phone with its code. The pin is read the way any client sees it (a TLS handshake against the
public origin), never from the certificate files; ITGLASSES_PAIRING_PIN overrides it.
"""
import base64
import hashlib
import os
import re
import socket
import ssl
import time
from urllib.parse import quote, urlsplit

SCHEME = "itglasses://emparejar"
VERSION = "1"
_ORIGIN = re.compile(r"https://[a-z0-9.-]+(:[0-9]+)?")
_PIN = re.compile(r"[A-Za-z0-9_-]{43}")
_CACHE_SECONDS = 600
_cache: dict[str, tuple[float, str]] = {}


class Unavailable(Exception):
    """The server's certificate could not be read (Caddy stopped, wrong address)."""


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def valid_origin(origin: str) -> bool:
    return bool(_ORIGIN.fullmatch(origin or ""))


def spki_pin(der: bytes) -> str:
    """base64url SHA-256 of the certificate's SubjectPublicKeyInfo (the same pin the phone computes)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    key = x509.load_der_x509_certificate(der).public_key()
    spki = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return b64(hashlib.sha256(spki).digest())


def presented_chain(origin: str, timeout: float = 5.0) -> list[bytes]:
    """The certificates the server presents at origin, leaf first, as any client sees them."""
    parts = urlsplit(origin)
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # Only reading what it presents; the phone does the checking.
    try:
        with socket.create_connection((parts.hostname, parts.port or 443), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=parts.hostname) as tls:
                chain = tls.get_unverified_chain() or []
    except (OSError, ssl.SSLError) as error:
        raise Unavailable() from error
    if not chain:
        raise Unavailable()
    return [bytes(der) for der in chain]


def server_pin(origin: str, now: float | None = None, fetch=presented_chain) -> str:
    configured = os.getenv("ITGLASSES_PAIRING_PIN", "").strip()
    if configured:
        if not _PIN.fullmatch(configured):
            raise Unavailable()
        return configured
    now = time.time() if now is None else now
    cached = _cache.get(origin)
    if cached and now - cached[0] < _CACHE_SECONDS:
        return cached[1]
    pin = spki_pin(fetch(origin)[-1])
    _cache[origin] = (now, pin)
    return pin


def link(origin: str, pin: str) -> str:
    return f"{SCHEME}?v={VERSION}&o={quote(origin, safe='')}&p={pin}"


def qr_svg(text: str) -> str:
    import segno

    return segno.make(text, error="m").svg_inline(scale=6, border=4, dark="#111111", light="#ffffff")
