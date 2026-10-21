from backend.app.providers.base import ProviderUnavailableError

class MockLLM:
    name = "mock"
    model = "mock-v1"

    async def is_available(self) -> bool:
        return True

    async def ensure_available(self) -> None:
        if not await self.is_available():
            raise ProviderUnavailableError()

    async def generate(self, message: str) -> str:
        return (
            "Mock response: no language model or documentation is connected. "
            "This is a connectivity test, not technical advice. "
            "The available documentation is insufficient to answer your question."
        )
