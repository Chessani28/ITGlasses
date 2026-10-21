from typing import Protocol


class ProviderUnavailableError(Exception):
    """The configured local provider is unavailable."""


class ProviderTimeoutError(Exception):
    """The configured local provider timed out."""


class LLMProvider(Protocol):
    name: str
    model: str

    async def is_available(self) -> bool: ...

    async def ensure_available(self) -> None: ...

    async def generate(self, message: str) -> str: ...


class ModelUnavailableError(ProviderUnavailableError):
    """The configured model is missing or is not a local model."""


class ProviderResponseError(Exception):
    """The local provider returned an invalid response."""


class ProviderOutputLimitError(ProviderResponseError):
    """Generation ended before a complete answer was available."""
