"""Arranque único (scripts/start-all.ps1, stop-all.ps1): las funciones de itglasses-services.ps1 se prueban en
PowerShell sin arrancar nada; los scripts se revisan por contenido y sintaxis."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="PowerShell not available")


def run(expression):
    """Dot-source the functions file and evaluate one expression; returns its JSON output."""
    script = f". '{SCRIPTS / 'itglasses-services.ps1'}'; {expression} | ConvertTo-Json -Compress"
    done = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    output = done.stdout.strip()
    return json.loads(output) if output else None


def test_the_caddy_bind_address_is_read_from_the_caddyfile(tmp_path):
    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text("{\n\tadmin off\n}\n\n:8443 {\n\tbind 172.16.5.142\n\ttls a b\n}\n", encoding="utf-8")
    assert run(f"Get-ITGlassesCaddyBind -Path '{caddyfile}'") == "172.16.5.142"


def test_a_missing_or_bindless_caddyfile_gives_nothing(tmp_path):
    assert run(f"Get-ITGlassesCaddyBind -Path '{tmp_path / 'none'}'") is None
    bindless = tmp_path / "Caddyfile"
    bindless.write_text(":8443 {\n}\n", encoding="utf-8")
    assert run(f"Get-ITGlassesCaddyBind -Path '{bindless}'") is None


def test_missing_ollama_models_are_listed():
    names = "@('qwen3:4b-instruct-2507-q4_K_M','qwen3:4b')"
    required = "@('qwen3:4b-instruct-2507-q4_K_M','qwen3-embedding:0.6b')"
    assert run(f"Get-ITGlassesMissingModels -Available {names} -Required {required}") == "qwen3-embedding:0.6b"
    complete = "@('qwen3:4b-instruct-2507-q4_K_M','qwen3-embedding:0.6b')"
    assert run(f"@(Get-ITGlassesMissingModels -Available {complete} -Required {required}).Count") == 0


def test_the_memory_line_warns_below_what_whisper_needs():
    assert run("Get-ITGlassesMemoryStatus -AvailableMB 7577") == "OK  7.4 GB libres"
    low = run("Get-ITGlassesMemoryStatus -AvailableMB 2900")
    assert low.startswith("POCA") and "2.8 GB" in low and "Vosk" in low


def test_start_all_reports_memory():
    assert "Get-ITGlassesMemoryStatus" in (SCRIPTS / "start-all.ps1").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["start-all.ps1", "stop-all.ps1", "itglasses-services.ps1", "stop-backend.ps1"])
def test_the_scripts_parse(name):
    path = SCRIPTS / name
    check = ("$errors = $null; [System.Management.Automation.Language.Parser]::ParseFile("
             f"'{path}', [ref]$null, [ref]$errors) | Out-Null; $errors.Count")
    done = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", check],
                          capture_output=True, text=True, timeout=60)
    assert done.stdout.strip() == "0", done.stdout + done.stderr


def test_start_all_reuses_healthy_services_and_never_kills_a_foreign_ollama():
    text = (SCRIPTS / "start-all.ps1").read_text(encoding="utf-8")
    assert "start-ollama.ps1" in text and "ensure-backend.ps1" in text and "caddy.exe" in text
    assert "Get-ITGlassesMissingModels" in text and "Get-ITGlassesCaddyBind" in text
    assert "Stop-Process" not in text  # a foreign Ollama is reported, never killed


def test_stop_backend_tolerates_a_process_that_already_exited():
    text = (SCRIPTS / "stop-backend.ps1").read_text(encoding="utf-8")
    assert "-ErrorAction SilentlyContinue" in text
