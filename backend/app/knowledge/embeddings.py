import asyncio
import httpx
import numpy as np
from .models import KnowledgeError


class OllamaEmbeddings:
    model = "qwen3-embedding:0.6b"

    def __init__(self, url="http://127.0.0.1:11434"):
        self.url = url
        self.lock = asyncio.Lock()

    async def fingerprint(self):
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
                response = await client.get(self.url + "/api/tags")
                response.raise_for_status()
                matches = [m for m in response.json()["models"] if m["name"] == self.model]
                if not matches or matches[0].get("remote_host"):
                    raise ValueError()
                return matches[0]["digest"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise KnowledgeError("embedding_unavailable", "The local embedding model is unavailable. No download or fallback was attempted.", 503) from None

    async def embed(self, texts, query=False):
        results = []
        async with self.lock:
            try:
                async with httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(120, connect=3)) as client:
                    for start in range(0, len(texts), 4):
                        inputs = texts[start:start + 4]
                        if query:
                            inputs = ["Instruct: Retrieve the company IT procedure relevant to the user's question.\nQuery: " + text for text in inputs]
                        response = await client.post(self.url + "/api/embed", json={
                            "model": self.model, "input": inputs, "truncate": False,
                            "keep_alive": "30s", "options": {"num_gpu": 0, "num_ctx": 2048, "num_thread": 4}})
                        response.raise_for_status()
                        vectors = np.asarray(response.json()["embeddings"], dtype=np.float32)
                        if vectors.ndim != 2 or vectors.shape[0] != len(inputs) or not np.isfinite(vectors).all():
                            raise ValueError()
                        lengths = np.linalg.norm(vectors, axis=1, keepdims=True)
                        if np.any(lengths == 0):
                            raise ValueError()
                        results.extend(vectors / lengths)
                return np.asarray(results)
            except httpx.TimeoutException:
                raise KnowledgeError("embedding_timeout", "Local embedding generation timed out.", 504) from None
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                raise KnowledgeError("embedding_error", "Local embedding generation failed.", 503) from None

    async def unload(self):
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
                await client.post(self.url + "/api/generate", json={"model": self.model, "keep_alive": 0})
        except httpx.HTTPError:
            pass
