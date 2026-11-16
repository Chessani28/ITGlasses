import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")


pytestmark = pytest.mark.skipif(
    os.name != "nt" or POWERSHELL is None,
    reason="Startup configuration tests require Windows PowerShell.",
)


def _quote_ps(value):
    return "'" + str(value).replace("'", "''") + "'"


def _run_runtime_config(project_root, environment=None):
    script = (
        "$ErrorActionPreference='Stop'; "
        f". {_quote_ps(ROOT / 'scripts' / 'runtime-config.ps1')}; "
        f"try {{ $null=Set-ITGlassesRuntimeConfiguration -ProjectRoot {_quote_ps(project_root)}; "
        "'CONFIG_OK' } catch { 'CONFIG_ERROR:' + $_.Exception.Message; exit 2 }"
    )
    env = os.environ.copy()
    for name in (
        "ITGLASSES_WHISPER_PYTHON",
        "ITGLASSES_WHISPER_MODEL",
        "ITGLASSES_REQUIRE_AUTH",
        "ITGLASSES_ALLOW_LAN_BINDING",
        "ITGLASSES_API_TOKENS",
        "ITGLASSES_HOST",
    ):
        env.pop(name, None)
    if environment:
        env.update(environment)
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
        check=False,
    )


def _configure_whisper(project_root):
    python = project_root / "voice-env" / "python.exe"
    model = project_root / "voice-model"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"test executable")
    model.mkdir()
    for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
        (model / name).write_bytes(b"test model file")
    return python, model


def test_valid_whisper_configuration_passes_and_preserves_ollama_local_defaults(tmp_path):
    python, model = _configure_whisper(tmp_path)
    (tmp_path / "runtime-config.psd1").write_text(
        "@{ WhisperPython = '%s'; WhisperModel = '%s'; RequireAuth = $false; AllowLanBinding = $false }"
        % (python, model),
        encoding="utf-8",
    )
    (tmp_path / "config.toml").write_text(
        'provider = "ollama"\nhost = "127.0.0.1"\nollama_url = "http://127.0.0.1:11434"\n',
        encoding="utf-8",
    )

    result = _run_runtime_config(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "CONFIG_OK" in result.stdout


def test_incomplete_whisper_paths_fail_with_sanitized_error(tmp_path):
    python, _ = _configure_whisper(tmp_path)
    secret_marker = "operator-private-path-marker"

    result = _run_runtime_config(
        tmp_path, {"ITGLASSES_WHISPER_PYTHON": str(python), "ITGLASSES_WHISPER_MODEL": ""}
    )

    assert result.returncode == 2
    assert "configuration is incomplete" in result.stdout
    assert secret_marker not in result.stdout + result.stderr


@pytest.mark.parametrize("environment", [
    {"ITGLASSES_WHISPER_PYTHON": "relative\\python.exe",
     "ITGLASSES_WHISPER_MODEL": "relative\\model"},
    {"ITGLASSES_WHISPER_PYTHON": "C:\\missing\\python.exe",
     "ITGLASSES_WHISPER_MODEL": "C:\\missing\\model"},
])
def test_invalid_whisper_paths_fail_closed(tmp_path, environment):
    result = _run_runtime_config(tmp_path, environment)

    assert result.returncode == 2
    assert "Whisper Small" in result.stdout
    assert "CONFIG_OK" not in result.stdout


def test_required_authentication_without_operator_token_fails_closed(tmp_path):
    (tmp_path / "runtime-config.psd1").write_text(
        "@{ WhisperPython = ''; WhisperModel = ''; RequireAuth = $true; AllowLanBinding = $false }",
        encoding="utf-8",
    )
    secret_marker = "never-print-this-token"

    result = _run_runtime_config(tmp_path)

    assert result.returncode == 2
    assert "authentication is required" in result.stdout
    assert secret_marker not in result.stdout + result.stderr


def test_required_authentication_loads_current_user_protected_token(tmp_path):
    (tmp_path / "runtime-config.psd1").write_text(
        "@{ WhisperPython = ''; WhisperModel = ''; RequireAuth = $true; AllowLanBinding = $false }",
        encoding="utf-8",
    )
    script = (
        "$ErrorActionPreference='Stop'; "
        f". {_quote_ps(ROOT / 'scripts' / 'runtime-config.ps1')}; "
        f"$root={_quote_ps(tmp_path)}; "
        "$secret=Join-Path $root '.runtime\\api-token.dpapi'; "
        "New-Item -ItemType Directory -Path (Split-Path $secret) -Force | Out-Null; "
        "$secure=ConvertTo-SecureString ([guid]::NewGuid().ToString()) -AsPlainText -Force; "
        "Set-Content -LiteralPath $secret -Value (ConvertFrom-SecureString $secure) -NoNewline; "
        "$null=Set-ITGlassesRuntimeConfiguration -ProjectRoot $root; "
        "if ($env:ITGLASSES_API_TOKENS) { 'TOKEN_LOADED' }"
    )
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", script],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "ITGLASSES_REQUIRE_AUTH": "",
            "ITGLASSES_API_TOKENS": "",
            "ITGLASSES_WHISPER_PYTHON": "",
            "ITGLASSES_WHISPER_MODEL": "",
        },
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "TOKEN_LOADED" in result.stdout
    assert len((tmp_path / ".runtime" / "api-token.dpapi").read_text()) > 20


def test_explicit_loopback_local_mode_needs_no_auth_token(tmp_path):
    (tmp_path / "runtime-config.psd1").write_text(
        "@{ WhisperPython = ''; WhisperModel = ''; RequireAuth = $false; AllowLanBinding = $false }",
        encoding="utf-8",
    )
    (tmp_path / "config.toml").write_text(
        'provider = "ollama"\nhost = "127.0.0.1"\nallow_lan_binding = false\n',
        encoding="utf-8",
    )

    result = _run_runtime_config(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "CONFIG_OK" in result.stdout


def test_environment_values_override_local_nonsecret_configuration(tmp_path):
    python, model = _configure_whisper(tmp_path)
    configured_python = tmp_path / "configured" / "python.exe"
    configured_model = tmp_path / "configured-model"
    configured_python.parent.mkdir()
    configured_python.write_bytes(b"configured executable")
    configured_model.mkdir()
    for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
        (configured_model / name).write_bytes(b"configured model")
    (tmp_path / "runtime-config.psd1").write_text(
        "@{ WhisperPython = '%s'; WhisperModel = '%s'; RequireAuth = $true; AllowLanBinding = $false }"
        % (configured_python, configured_model),
        encoding="utf-8",
    )

    result = _run_runtime_config(tmp_path, {
        "ITGLASSES_WHISPER_PYTHON": str(python),
        "ITGLASSES_WHISPER_MODEL": str(model),
        "ITGLASSES_REQUIRE_AUTH": "false",
        "ITGLASSES_API_TOKENS": "test-only-token",
    })

    assert result.returncode == 0, result.stdout + result.stderr
    assert "CONFIG_OK" in result.stdout
    assert "test-only-token" not in result.stdout + result.stderr


def test_invalid_configuration_error_does_not_expose_inherited_token(tmp_path):
    secret = "operator-token-must-not-appear"
    result = _run_runtime_config(tmp_path, {
        "ITGLASSES_WHISPER_PYTHON": "relative\\python.exe",
        "ITGLASSES_WHISPER_MODEL": "relative\\model",
        "ITGLASSES_API_TOKENS": secret,
    })

    assert result.returncode == 2
    assert secret not in result.stdout + result.stderr


def test_legacy_environment_startup_without_runtime_file_remains_supported(tmp_path):
    result = _run_runtime_config(tmp_path, {
        "ITGLASSES_WHISPER_PYTHON": "",
        "ITGLASSES_WHISPER_MODEL": "",
    })

    assert result.returncode == 0, result.stdout + result.stderr
    assert "CONFIG_OK" in result.stdout


def test_unknown_local_configuration_keys_are_rejected(tmp_path):
    (tmp_path / "runtime-config.psd1").write_text(
        "@{ ApiToken = 'must-not-be-supported' }",
        encoding="utf-8",
    )

    result = _run_runtime_config(tmp_path)

    assert result.returncode == 2
    assert "unsupported keys" in result.stdout
    assert "must-not-be-supported" not in result.stdout + result.stderr
