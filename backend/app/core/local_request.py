"""Single definition of a direct local request: this PC, no proxy in between.

Used by the credential-free local mode (api/auth.py) and the admin gate
(pairing/admin.py). Behind Caddy, uvicorn may rewrite request.client from
X-Forwarded-For, but the forwarding headers stay visible, so relayed traffic
is never treated as local whatever address it claims.
"""
import ipaddress

from backend.app.core.config import settings

LOOPBACK = {"127.0.0.1", "::1"}
PROXY_HEADERS = ("forwarded", "via", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip")


def is_loopback(address):
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return str(ip) in LOOPBACK


def is_direct_local(request):
    """True only for a loopback peer whose request carries no forwarding header."""
    client = request.client.host if request.client else ""
    return is_loopback(client) and not any(name in request.headers for name in PROXY_HEADERS)


def local_hosts():
    """Host header values that name this backend directly (defeats DNS rebinding and proxied hosts)."""
    port = settings.port
    return {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}


def has_local_host(request):
    return request.headers.get("host", "").lower() in local_hosts()
