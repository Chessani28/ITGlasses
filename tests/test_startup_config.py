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
        "ITGLASSES_PAIRING_ORIGIN",
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


def _run_and_print_origin(project_root, environment=None):
    """Like _run_runtime_config, then print the pairing origin the backend would receive."""
    script = (
        "$ErrorActionPreference='Stop'; "
        f". {_quote_ps(ROOT / 'scripts' / 'runtime-config.ps1')}; "
        f"try {{ $null=Set-ITGlassesRuntimeConfiguration -ProjectRoot {_quote_ps(project_root)}; "
        "'ORIGIN=[' + $env:ITGLASSES_PAIRING_ORIGIN + ']' } "
        "catch { 'CONFIG_ERROR:' + $_.Exception.Message; exit 2 }"
    )
    env = {**os.environ, "ITGLASSES_WHISPER_PYTHON": "", "ITGLASSES_WHISPER_MODEL": "",
           "ITGLASSES_REQUIRE_AUTH": "", "ITGLASSES_ALLOW_LAN_BINDING": "", "ITGLASSES_API_TOKENS": "",
           "ITGLASSES_PAIRING_ORIGIN": "", **(environment or {})}
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, env=env, timeout=20, check=False,
    )


def _write_origin_config(project_root, value):
    (project_root / "runtime-config.psd1").write_text(
        "@{ WhisperPython = ''; WhisperModel = ''; RequireAuth = $false; AllowLanBinding = $false; "
        f"PairingOrigin = {value} }}",
        encoding="utf-8",
    )


def test_pairing_origin_from_configuration_is_exported(tmp_path):
    _write_origin_config(tmp_path, "'https://172.16.5.143:8443'")

    result = _run_and_print_origin(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "ORIGIN=[https://172.16.5.143:8443]" in result.stdout


def test_environment_pairing_origin_wins_over_configuration(tmp_path):
    _write_origin_config(tmp_path, "'https://172.16.5.143:8443'")

    result = _run_and_print_origin(tmp_path, {"ITGLASSES_PAIRING_ORIGIN": "https://10.0.0.5:9443"})

    assert result.returncode == 0, result.stdout + result.stderr
    assert "ORIGIN=[https://10.0.0.5:9443]" in result.stdout


def test_missing_or_empty_pairing_origin_stays_off(tmp_path):
    result = _run_and_print_origin(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ORIGIN=[]" in result.stdout

    _write_origin_config(tmp_path, "''")
    result = _run_and_print_origin(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ORIGIN=[]" in result.stdout


def test_invalid_pairing_origin_stops_startup(tmp_path):
    for value in ("'http://172.16.5.143:8443'", "'https://172.16.5.143:8443/'", "'https://HOST:8443'",
                  "'https://172.16.5.143:443'", "'https://172.16.5.143 :8443'", "$true"):
        _write_origin_config(tmp_path, value)

        result = _run_and_print_origin(tmp_path)

        assert result.returncode == 2, value
        assert "Pairing origin" in result.stdout or "PairingOrigin" in result.stdout, value
        assert "ORIGIN=[" not in result.stdout, value


def _run_and_print_keep(project_root, value, environment=None):
    (project_root / "runtime-config.psd1").write_text(
        "@{ WhisperPython = ''; WhisperModel = ''; RequireAuth = $false; AllowLanBinding = $false; "
        f"PairingOrigin = ''; WhisperKeepRamGiB = {value} }}",
        encoding="utf-8",
    )
    script = (
        "$ErrorActionPreference='Stop'; "
        f". {_quote_ps(ROOT / 'scripts' / 'runtime-config.ps1')}; "
        f"try {{ $null=Set-ITGlassesRuntimeConfiguration -ProjectRoot {_quote_ps(project_root)}; "
        "'KEEP=[' + $env:ITGLASSES_WHISPER_KEEP_RAM_GIB + ']' } "
        "catch { 'CONFIG_ERROR:' + $_.Exception.Message; exit 2 }"
    )
    env = {**os.environ, "ITGLASSES_WHISPER_PYTHON": "", "ITGLASSES_WHISPER_MODEL": "",
           "ITGLASSES_REQUIRE_AUTH": "", "ITGLASSES_ALLOW_LAN_BINDING": "", "ITGLASSES_API_TOKENS": "",
           "ITGLASSES_PAIRING_ORIGIN": "", "ITGLASSES_WHISPER_KEEP_RAM_GIB": "", **(environment or {})}
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, env=env, timeout=20, check=False,
    )


def test_whisper_keep_ram_from_configuration_is_exported_with_a_dot(tmp_path):
    for value, expected in (("6", "6"), ("6.5", "6.5")):
        result = _run_and_print_keep(tmp_path, value)
        assert result.returncode == 0, result.stdout + result.stderr
        assert f"KEEP=[{expected}]" in result.stdout, value


def test_environment_whisper_keep_ram_wins_over_configuration(tmp_path):
    result = _run_and_print_keep(tmp_path, "6", {"ITGLASSES_WHISPER_KEEP_RAM_GIB": "10"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "KEEP=[10]" in result.stdout


def test_invalid_whisper_keep_ram_stops_startup(tmp_path):
    for value in ("0", "-2", "'6'", "$true"):
        result = _run_and_print_keep(tmp_path, value)
        assert result.returncode == 2, value
        assert "WhisperKeepRamGiB" in result.stdout, value
        assert "KEEP=[" not in result.stdout, value


def _run_and_print_voice2(project_root, entries, environment=None):
    (project_root / "runtime-config.psd1").write_text(
        "@{ WhisperPython = ''; WhisperModel = ''; RequireAuth = $false; AllowLanBinding = $false; "
        f"PairingOrigin = ''; {entries} }}",
        encoding="utf-8",
    )
    script = (
        "$ErrorActionPreference='Stop'; "
        f". {_quote_ps(ROOT / 'scripts' / 'runtime-config.ps1')}; "
        f"try {{ $null=Set-ITGlassesRuntimeConfiguration -ProjectRoot {_quote_ps(project_root)}; "
        "'HINT=[' + $env:ITGLASSES_WHISPER_HINT_MODE + '] CORRECTION=[' + $env:ITGLASSES_STT_CORRECTION + ']' } "
        "catch { 'CONFIG_ERROR:' + $_.Exception.Message; exit 2 }"
    )
    env = {**os.environ, "ITGLASSES_WHISPER_PYTHON": "", "ITGLASSES_WHISPER_MODEL": "",
           "ITGLASSES_REQUIRE_AUTH": "", "ITGLASSES_ALLOW_LAN_BINDING": "", "ITGLASSES_API_TOKENS": "",
           "ITGLASSES_PAIRING_ORIGIN": "", "ITGLASSES_WHISPER_HINT_MODE": "", "ITGLASSES_STT_CORRECTION": "",
           **(environment or {})}
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, env=env, timeout=20, check=False,
    )


def test_voice2_settings_from_configuration_are_exported(tmp_path):
    result = _run_and_print_voice2(tmp_path, "WhisperHintMode = 'initial_prompt'; SttCorrection = $false")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HINT=[initial_prompt] CORRECTION=[false]" in result.stdout


def test_voice2_environment_wins_and_absent_keys_stay_unset(tmp_path):
    result = _run_and_print_voice2(tmp_path, "WhisperHintMode = 'off'", {"ITGLASSES_WHISPER_HINT_MODE": "hotwords"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HINT=[hotwords] CORRECTION=[]" in result.stdout


def test_invalid_voice2_settings_stop_startup(tmp_path):
    for entries, key in (("WhisperHintMode = 'prompt'", "WhisperHintMode"), ("WhisperHintMode = 1", "WhisperHintMode"),
                         ("SttCorrection = 'yes'", "SttCorrection")):
        result = _run_and_print_voice2(tmp_path, entries)
        assert result.returncode == 2, entries
        assert key in result.stdout, entries



VOZ3_ENVIRONMENT = ("ITGLASSES_WHISPER_DEVICE", "ITGLASSES_WHISPER_COMPUTE_TYPE", "ITGLASSES_WHISPER_FALLBACK_MODEL",
                    "ITGLASSES_WHISPER_PRELOAD", "ITGLASSES_WHISPER_MIN_VRAM_MIB", "ITGLASSES_WHISPER_IDLE_SECONDS")


def _run_and_print_voice3(project_root, entries, environment=None):
    (project_root / "runtime-config.psd1").write_text(
        "@{ WhisperPython = ''; WhisperModel = ''; RequireAuth = $false; AllowLanBinding = $false; "
        f"PairingOrigin = ''; {entries} }}",
        encoding="utf-8",
    )
    printed = " + ' ' + ".join(
        f"'{name[len('ITGLASSES_WHISPER_'):]}=[' + $env:{name} + ']'" for name in VOZ3_ENVIRONMENT
    )
    script = (
        "$ErrorActionPreference='Stop'; "
        f". {_quote_ps(ROOT / 'scripts' / 'runtime-config.ps1')}; "
        f"try {{ $null=Set-ITGlassesRuntimeConfiguration -ProjectRoot {_quote_ps(project_root)}; "
        f"({printed}) }} "
        "catch { 'CONFIG_ERROR:' + $_.Exception.Message; exit 2 }"
    )
    env = {**os.environ, "ITGLASSES_WHISPER_PYTHON": "", "ITGLASSES_WHISPER_MODEL": "",
           "ITGLASSES_REQUIRE_AUTH": "", "ITGLASSES_ALLOW_LAN_BINDING": "", "ITGLASSES_API_TOKENS": "",
           "ITGLASSES_PAIRING_ORIGIN": "", **{name: "" for name in VOZ3_ENVIRONMENT}, **(environment or {})}
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, env=env, timeout=20, check=False,
    )


def _whisper_model(folder, files):
    folder.mkdir(parents=True)
    for name in files:
        (folder / name).write_bytes(b"test model file")
    return folder


def test_voice3_gpu_settings_from_configuration_are_exported(tmp_path):
    small = _whisper_model(tmp_path / "small", ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"))
    result = _run_and_print_voice3(
        tmp_path,
        "WhisperDevice = 'cuda'; WhisperComputeType = 'int8_float16'; WhisperFallbackModel = %s; "
        "WhisperPreload = $false; WhisperMinVramMiB = 1000; WhisperIdleSeconds = 900" % _quote_ps(small),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DEVICE=[cuda]" in result.stdout and "COMPUTE_TYPE=[int8_float16]" in result.stdout
    assert f"FALLBACK_MODEL=[{small}]" in result.stdout and "PRELOAD=[false]" in result.stdout
    assert "MIN_VRAM_MIB=[1000]" in result.stdout and "IDLE_SECONDS=[900]" in result.stdout


def test_voice3_environment_wins_and_absent_keys_stay_unset(tmp_path):
    result = _run_and_print_voice3(tmp_path, "WhisperDevice = 'cuda'", {"ITGLASSES_WHISPER_DEVICE": "cpu"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DEVICE=[cpu]" in result.stdout and "COMPUTE_TYPE=[]" in result.stdout and "PRELOAD=[]" in result.stdout


def test_invalid_voice3_settings_stop_startup(tmp_path):
    broken = _whisper_model(tmp_path / "broken", ("model.bin", "config.json", "tokenizer.json", "vocabulary.json"))
    for entries, key in (
        ("WhisperDevice = 'gpu'", "WhisperDevice"),
        ("WhisperDevice = 'cuda'; WhisperComputeType = 'float32'", "WhisperComputeType"),
        ("WhisperComputeType = 'int8_float16'", "WhisperComputeType"),           # not valid on the default cpu
        ("WhisperPreload = 'yes'", "WhisperPreload"),
        ("WhisperMinVramMiB = -1", "WhisperMinVramMiB"),
        ("WhisperMinVramMiB = '1000'", "WhisperMinVramMiB"),
        ("WhisperIdleSeconds = 10", "WhisperIdleSeconds"),
        ("WhisperFallbackModel = 'relative\\small'", "fallback model"),
        ("WhisperFallbackModel = %s" % _quote_ps(broken), "fallback model"),     # vocabulary.json without preprocessor
    ):
        result = _run_and_print_voice3(tmp_path, entries)
        assert result.returncode == 2, entries
        assert key in result.stdout, entries


def test_turbo_model_folder_with_vocabulary_json_is_accepted(tmp_path):
    python, _ = _configure_whisper(tmp_path)
    turbo = _whisper_model(tmp_path / "turbo", ("model.bin", "config.json", "tokenizer.json", "vocabulary.json",
                                                "preprocessor_config.json"))
    result = _run_runtime_config(tmp_path, {"ITGLASSES_WHISPER_PYTHON": str(python),
                                            "ITGLASSES_WHISPER_MODEL": str(turbo)})
    assert result.returncode == 0, result.stdout + result.stderr
    (turbo / "preprocessor_config.json").unlink()
    result = _run_runtime_config(tmp_path, {"ITGLASSES_WHISPER_PYTHON": str(python),
                                            "ITGLASSES_WHISPER_MODEL": str(turbo)})
    assert result.returncode == 2 and "incomplete" in result.stdout


def _run_and_print_password_minimum(project_root, entries, environment=None):
    (project_root / "runtime-config.psd1").write_text(
        "@{ WhisperPython = ''; WhisperModel = ''; RequireAuth = $false; AllowLanBinding = $false; "
        f"PairingOrigin = ''; {entries} }}",
        encoding="utf-8",
    )
    script = (
        "$ErrorActionPreference='Stop'; "
        f". {_quote_ps(ROOT / 'scripts' / 'runtime-config.ps1')}; "
        f"try {{ $null=Set-ITGlassesRuntimeConfiguration -ProjectRoot {_quote_ps(project_root)}; "
        "'MIN=[' + $env:ITGLASSES_PASSWORD_MIN_LENGTH + ']' } "
        "catch { 'CONFIG_ERROR:' + $_.Exception.Message; exit 2 }"
    )
    env = {**os.environ, "ITGLASSES_WHISPER_PYTHON": "", "ITGLASSES_WHISPER_MODEL": "",
           "ITGLASSES_REQUIRE_AUTH": "", "ITGLASSES_ALLOW_LAN_BINDING": "", "ITGLASSES_API_TOKENS": "",
           "ITGLASSES_PAIRING_ORIGIN": "", "ITGLASSES_PASSWORD_MIN_LENGTH": "", **(environment or {})}
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, env=env, timeout=20, check=False,
    )


def test_password_minimum_from_configuration_is_exported(tmp_path):
    result = _run_and_print_password_minimum(tmp_path, "PasswordMinLength = 12")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "MIN=[12]" in result.stdout
    result = _run_and_print_password_minimum(tmp_path, "PasswordMinLength = 12", {"ITGLASSES_PASSWORD_MIN_LENGTH": "8"})
    assert "MIN=[8]" in result.stdout  # the environment wins
    result = _run_and_print_password_minimum(tmp_path, "WhisperIdleSeconds = 900")
    assert "MIN=[]" in result.stdout  # absent: the backend uses its default (5)


@pytest.mark.parametrize("entry", ["PasswordMinLength = 4", "PasswordMinLength = '8'"])
def test_invalid_password_minimum_stops_startup(tmp_path, entry):
    result = _run_and_print_password_minimum(tmp_path, entry)
    assert result.returncode == 2 and "PasswordMinLength" in result.stdout
