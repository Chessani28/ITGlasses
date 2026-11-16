"""Verify ES256 DPoP proofs. Pairing parameters live in the signed payload."""
import base64
import hashlib
import json
import re
import sqlite3
import time

from fastapi import HTTPException

from .store import origin


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def decode(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("invalid base64url")
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def unique_object(items):
    obj = dict(items)
    if len(obj) != len(items):
        raise ValueError("duplicate JSON key")
    return obj


def proof(request, db, token=None):
    target = origin(request) + request.url.path
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, utils

    raw = request.headers.get("dpop", "")
    now = int(time.time())
    try:
        if len(raw) > 4096 or request.url.query:
            raise ValueError()
        head, payload, sig = raw.split(".")
        header, claims = [json.loads(decode(part), object_pairs_hook=unique_object) for part in (head,payload)]
        jwk = header["jwk"]
        if (set(header) != {"typ","alg","jwk"} or header["typ"] != "dpop+jwt"
                or header["alg"] != "ES256" or set(jwk) != {"kty","crv","x","y"}
                or jwk["kty"] != "EC" or jwk["crv"] != "P-256"):
            raise ValueError()
        coords = [decode(jwk[a]) for a in ("x","y")]
        if any(len(v) != 32 or b64(v) != jwk[a] for a,v in zip(("x","y"),coords)):
            raise ValueError()
        key = ec.EllipticCurvePublicNumbers(*[int.from_bytes(v) for v in coords], ec.SECP256R1()).public_key()
        signature = decode(sig)
        if len(signature) != 64:
            raise ValueError()
        der = utils.encode_dss_signature(int.from_bytes(signature[:32]), int.from_bytes(signature[32:]))
        key.verify(der, (head+"."+payload).encode(), ec.ECDSA(hashes.SHA256()))
        if (claims["htm"] != request.method or claims["htu"] != target
                or type(claims["iat"]) is not int or abs(now-claims["iat"]) > 60
                or not re.fullmatch(r"[a-zA-Z0-9_-]{16,80}", claims["jti"])):
            raise ValueError()
        if token is not None and claims.get("ath") != b64(hashlib.sha256(token.encode()).digest()):
            raise ValueError()
        fingerprint = b64(hashlib.sha256(json.dumps(jwk,sort_keys=True,separators=(",",":")).encode()).digest())
        db.execute("DELETE FROM seen WHERE expires<=?", (now,))
        db.execute("INSERT INTO seen VALUES(?,?,?)", (fingerprint,claims["jti"], now+125))
        return fingerprint, claims
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError, InvalidSignature, sqlite3.IntegrityError):
        raise HTTPException(401) from None

