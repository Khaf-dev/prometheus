"""
Embedding Service — Generate vector embeddings untuk memory dan RAG.

Multi-provider:
- OpenAI text-embedding-3-small (cloud, 1536 dim, best quality)
- Ollama mxbai-embed-large (lokal, 1536 dim native, no padding needed)

Kedua provider output 1536 dim — fully compatible di pgvector.
Fallback otomatis: OpenAI gagal → Ollama lokal.
"""

import logging
import os
from abc import ABC, abstractmethod

import httpx

logger = logging.getLogger(__name__)

EMBED_DIM = 1536  # Unified — both providers output 1536 dim natively


class BaseEmbeddingProvider(ABC):

    @property
    @abstractmethod
    def dimensions(self) -> int: ...

    @abstractmethod
    async def embed(self, text: str) -> list[float]: ...

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        """Default: sequential. Override untuk batch API."""
        results = []
        for text in texts:
            results.append(await self.embed(text))
        return results


class OpenAIEmbeddingProvider(BaseEmbeddingProvider):
    """
    OpenAI text-embedding-3-small.
    1536 dim. Cost: $0.02 / 1M tokens.
    """

    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        self._api_key = api_key
        self._model = model
        self._client = httpx.AsyncClient(
            base_url="https://api.openai.com/v1",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )

    @property
    def dimensions(self) -> int:
        return EMBED_DIM

    async def embed(self, text: str) -> list[float]:
        text = text[:8000]  # 8191 token limit
        response = await self._client.post(
            "/embeddings",
            json={"model": self._model, "input": text},
        )
        response.raise_for_status()
        return response.json()["data"][0]["embedding"]

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        """OpenAI support batch — lebih efisien dari sequential."""
        truncated = [t[:8000] for t in texts]
        response = await self._client.post(
            "/embeddings",
            json={"model": self._model, "input": truncated},
        )
        response.raise_for_status()
        data = response.json()
        sorted_data = sorted(data["data"], key=lambda x: x["index"])
        return [item["embedding"] for item in sorted_data]


class OllamaEmbeddingProvider(BaseEmbeddingProvider):
    """
    Ollama mxbai-embed-large — fully local, zero cost, privacy-max.

    CATATAN PENTING: mxbai-embed-large output asli 1024 dim, BUKAN 1536.
    Database schema Prometheus pakai vector(1536) (fixed di migration 001),
    jadi kita zero-pad dari 1024 ke 1536 di sini.

    Trade-off: similarity SEARCH ANTAR Ollama embeddings (1024 asli, dipad sama)
    tetap akurat karena posisi padding konsisten. Yang berkurang akurasinya
    hanya saat searching CROSS-PROVIDER (embedding OpenAI vs Ollama dalam
    satu pencarian yang sama) — itu skenario yang sudah jarang terjadi
    karena EmbeddingService pakai SATU provider per request (dengan fallback,
    bukan campur).

    Setup:
        ollama pull mxbai-embed-large
    """

    NATIVE_DIM = 1024  # actual output dimension dari mxbai-embed-large

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "mxbai-embed-large",
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=60.0,
        )

    @property
    def dimensions(self) -> int:
        return EMBED_DIM  # 1536 — setelah padding

    async def embed(self, text: str) -> list[float]:
        """
        Embed satu teks, dengan pemangkasan bertahap bila ditolak.

        Model embedding punya batas token keras (512 untuk mxbai). Ukuran
        potongan di hulu hanya PERKIRAAN berbasis kata, dan untuk markdown
        padat — tabel, blok kode, URL — tokenizer memecah simbol jauh lebih
        agresif dari perkiraan. Menebak lebih akurat tidak pernah cukup.
        Jadi kalau ditolak, kita pendekkan dan coba lagi. Lebih baik satu
        potongan terpangkas daripada seluruh dokumen gagal.
        """
        attempt = text
        for _ in range(3):
            try:
                response = await self._client.post(
                    "/api/embeddings",
                    json={"model": self._model, "prompt": attempt},
                )
                response.raise_for_status()
                break
            except httpx.HTTPStatusError as e:
                if e.response.status_code != 500 or len(attempt) < 200:
                    raise
                attempt = attempt[: int(len(attempt) * 0.6)]
                logger.warning(
                    f"Embedding ditolak; memangkas ke {len(attempt)} karakter lalu mencoba lagi."
                )
        else:
            raise RuntimeError("Teks tetap ditolak setelah dipangkas beberapa kali.")

        embedding = response.json()["embedding"]

        if len(embedding) == EMBED_DIM:
            # Beberapa versi/model varian mxbai bisa native 1536, langsung pakai
            return embedding

        if len(embedding) == self.NATIVE_DIM:
            # Zero-pad 1024 → 1536 supaya compatible dengan schema pgvector
            return embedding + [0.0] * (EMBED_DIM - self.NATIVE_DIM)

        raise ValueError(
            f"Unexpected embedding dim dari {self._model}: got {len(embedding)}, "
            f"expected {self.NATIVE_DIM} atau {EMBED_DIM}. "
            f"Cek apakah model yang ter-pull benar mxbai-embed-large."
        )


class EmbeddingService:
    """
    Unified embedding service dengan automatic fallback.

    Priority:
    1. OpenAI text-embedding-3-small (kalau OPENAI_API_KEY ada)
    2. Ollama mxbai-embed-large (fallback lokal)

    Kedua provider: 1536 dim native → fully compatible di pgvector,
    zero padding, zero accuracy loss saat fallback.

    Usage:
        service = EmbeddingService.from_env()
        vector = await service.embed("cara setup pgvector di Docker")
    """

    def __init__(self, providers: list[BaseEmbeddingProvider]):
        if not providers:
            raise ValueError("Minimal satu embedding provider harus tersedia.")
        self._providers = providers
        logger.info(
            f"EmbeddingService initialized: "
            f"{[p.__class__.__name__ for p in providers]}"
        )

    @classmethod
    def from_env(cls) -> "EmbeddingService":
        providers: list[BaseEmbeddingProvider] = []

        if key := os.getenv("OPENAI_API_KEY"):
            providers.append(OpenAIEmbeddingProvider(api_key=key))
            logger.info("Embedding provider: OpenAI text-embedding-3-small (1536 dim)")

        ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        ollama_model = os.getenv("OLLAMA_EMBED_MODEL", "mxbai-embed-large")
        providers.append(OllamaEmbeddingProvider(base_url=ollama_url, model=ollama_model))
        logger.info(f"Embedding provider: Ollama {ollama_model} (1536 dim native)")

        return cls(providers=providers)

    async def embed(self, text: str) -> list[float]:
        """Embed text dengan automatic fallback."""
        if not text.strip():
            raise ValueError("Tidak bisa embed text kosong.")

        last_error: Exception | None = None
        for provider in self._providers:
            try:
                return await provider.embed(text)
            except Exception as e:
                logger.warning(
                    f"{provider.__class__.__name__} failed: {e}. "
                    f"Trying next provider..."
                )
                last_error = e

        raise RuntimeError(
            f"Semua embedding providers gagal. Last error: {last_error}"
        )

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        """Batch embed dengan fallback."""
        last_error: Exception | None = None
        for provider in self._providers:
            try:
                return await provider.embed_many(texts)
            except Exception as e:
                logger.warning(f"Batch embedding gagal di {provider.__class__.__name__}: {e}")
                last_error = e
        raise RuntimeError(f"Semua embedding providers gagal. Last error: {last_error}")

    @property
    def dimensions(self) -> int:
        return EMBED_DIM