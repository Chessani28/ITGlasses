"""Stage 6: the Caddyfile publishes exactly the phone routes, generated from the backend."""
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from backend.app.pairing.routes import MOBILE, router

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "caddy-config.py"
_spec = importlib.util.spec_from_file_location("caddy_config", SCRIPT)
caddy_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(caddy_config)

CERT, KEY = "C:/ITGlasses-Caddy/certs/server.crt", "C:/ITGlasses-Caddy/certs/server.key"
MATCHER = re.compile(r"\t@phone_(\w+) \{\n\t\tmethod (\w+)\n\t\tpath ([^\n]+)\n\t\}")


def generated():
    return caddy_config.caddyfile("172.16.5.143", 8443, CERT, KEY)


def published(text):
    routes = set()
    for name, method, paths in MATCHER.findall(text):
        assert name == method.lower()
        routes |= {(method, path) for path in paths.split()}
    return routes


def test_published_routes_are_exactly_the_phone_routes():
    expected = set(MOBILE) | {(m, r.path) for r in router.routes for m in r.methods} | {("GET", "/health")}
    routes = published(generated())
    assert routes == expected
    # Router paths carry the /api/v1 prefix; a Caddyfile without it would publish dead routes.
    assert all(path == "/health" or path.startswith("/api/v1/") for _, path in routes), routes
    assert {("POST", "/api/v1/pairing/requests"), ("GET", "/api/v1/devices/me"), ("POST", "/api/v1/chat")} <= routes
    assert ("GET", "/health") in routes and ("POST", "/health") not in routes


def test_everything_else_is_404_and_the_site_is_locked_down():
    text = generated()
    assert text.rstrip().endswith("\thandle {\n\t\trespond 404\n\t}\n}")
    assert text.count("respond 404") == 1
    assert text.index("handle @phone_") < text.index("\thandle {\n\t\trespond 404")
    for needed in ("admin off", "auto_https disable_redirects", ":8443 {", "bind 172.16.5.143",
                   f"tls {CERT} {KEY}", "max_size 2MB", "reverse_proxy 127.0.0.1:",
                   "dial_timeout 5s", "response_header_timeout 150s"):
        assert needed in text, needed
    assert "/api/v1/admin" not in text and "*" not in text
    assert not re.search(r"^\s*log\b", text, re.MULTILINE)  # No access log (DPoP headers, ids).


@pytest.mark.parametrize("route", [("GET", "/api/v1/admin/devices"), ("GET", "/api/v1/pairing/*"),
                                   ("GET", "/api/v1/documents/{doc_id}"), ("POST", "/pairing/requests")])
def test_refuses_to_publish_admin_wildcards_parameters_or_unprefixed_paths(route):
    with pytest.raises(SystemExit):
        caddy_config.caddyfile("172.16.5.143", 8443, CERT, KEY, routes={route}, upstream=8000)


def test_output_is_utf8_without_bom_and_with_lf(tmp_path):
    target = tmp_path / "Caddyfile"
    caddy_config.main(["--output", str(target)])
    raw = target.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw
    assert raw.decode("utf-8") == generated()


def test_script_runs_as_a_program_from_any_directory(tmp_path):
    # As the operator runs it: a separate process, another working directory, no PYTHONPATH.
    target = tmp_path / "Caddyfile"
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    result = subprocess.run([sys.executable, str(SCRIPT), "--output", str(target)], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert target.read_bytes().decode("utf-8") == generated()

