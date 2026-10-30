from dataclasses import dataclass
from pathlib import Path
import os
import tomllib
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Settings:
    app_name: str = "ITGlasses"
    version: str = "0.2.0"
    host: str = "127.0.0.1"
    port: int = 8000
    allow_lan_binding: bool = False
    provider: str = "ollama"
    ollama_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3:4b-instruct-2507-q4_K_M"
    context_length: int = 2048
    max_output_tokens: int = 256
    thinking: bool = False
    timeout_seconds: float = 120.0
    health_timeout_seconds: float = 5.0
    keep_alive: str = "30s"

    def __post_init__(self):
        if self.provider not in {"mock", "ollama"}:
            raise ValueError("Provider must be explicitly mock or ollama")
        if not isinstance(self.thinking, bool):
            raise ValueError("Thinking must be a boolean")
        url = urlsplit(self.ollama_url)
        if (url.scheme != "http" or url.hostname != "127.0.0.1"
                or url.username or url.password or url.path not in {"", "/"}
                or url.query or url.fragment or not url.port):
            raise ValueError("Ollama URL must be an HTTP IPv4 loopback origin with a port")
        if not self.model or "cloud" in self.model.lower():
            raise ValueError("A local model name is required")
        if self.host != "127.0.0.1" and not self.allow_lan_binding:
            raise ValueError("Non-loopback binding requires ITGLASSES_ALLOW_LAN_BINDING=true")
        if not 512 <= self.context_length <= 4096:
            raise ValueError("Context must be between 512 and 4096 tokens")
        if not 1 <= self.max_output_tokens <= 1024:
            raise ValueError("Output must be between 1 and 1024 tokens")
        if not 1 <= self.timeout_seconds <= 300 or not 1 <= self.health_timeout_seconds <= 10:
            raise ValueError("Timeout outside supported bounds")


def load_settings(path: Path = PROJECT_ROOT / "config.toml") -> Settings:
    data = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if "ITGLASSES_HOST" in os.environ:
        data["host"] = os.environ["ITGLASSES_HOST"]
    if "ITGLASSES_PORT" in os.environ:
        data["port"] = int(os.environ["ITGLASSES_PORT"])
    if "ITGLASSES_PROVIDER" in os.environ:
        data["provider"] = os.environ["ITGLASSES_PROVIDER"]
    if "OLLAMA_BASE_URL" in os.environ:
        data["ollama_url"] = os.environ["OLLAMA_BASE_URL"]
    if "ITGLASSES_ALLOW_LAN_BINDING" in os.environ:
        data["allow_lan_binding"] = os.environ["ITGLASSES_ALLOW_LAN_BINDING"].lower() in {"1", "true", "yes"}
    return Settings(**data)


settings = load_settings()