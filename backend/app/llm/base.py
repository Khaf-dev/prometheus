"""
Base abstract class untuk semua LLM providers.
Setiap provider WAJIB implement interface ini — zero magic, full control.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator


class ProviderName(str, Enum):
    CLAUDE = "claude"
    GPT40 = "gpt4o"
    GEMINI = "gemini"
    OLLAMA = "ollama"

class TaskType(str, Enum):
    """
    Dipakai untuk auto-routing ke provider yang paling optimal.
    Bisa di-override manual oleh user.
    """
    CODING = "coding"       # -> Claude (best reasoning)
    RESEARCH = "research"   # -> Gemini (large context window)
    QUICK = "quick"         # -> Ollama lokal (hemat cost)
    GENERAL = "general"     # -> default provider

@dataclass
class Message:
    role: str
    content: str

@dataclass
class LLMConfig:
     """Config per-request. Semua optional, ada default di gateway."""
     provider: ProviderName | None = None
     task_type: TaskType = TaskType.GENERAL
     model: str | None = None
     temperature: float = 0.7
     max_tokens: int = 4096
     stream: bool =True
     system_prompt: str | None = None

@dataclass
class LLMResponse:
    content: str
    provider: ProviderName
    model: str
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass
class StreamChunk:
    delta: str
    provider: ProviderName
    is_done: bool = False
    usage: dict = field(default_factory=dict)

class BaseLLMProvider(ABC):
    """
    Abstract base class untuk semua LLM providers di Prometheus.

    Cara tambah provider baru:
        1. Buat file baru: app/llm/providers/my_provider.py
        2. Inherit class ini
        3. Implement ketiga method abstract di bawah
        4. Register di LLMGateway._build_providers()

    That's it — zero perubahan di core gateway.
    """

    def __init__(self, api_key: str, default_model: str):
        self.api_key = api_key
        self.default_model = default_model

    @property
    @abstractmethod
    def provider_name(self) -> ProviderName:
        """Return enum name provider ini."""
        ...

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        config: LLMConfig,
    ) -> LLMResponse:
        ...

    @abstractmethod
    async def stream(
        self,
        messages: list[Message],
        config: LLMConfig,
    ) -> AsyncIterator[StreamChunk]:
        """
        Streaming completion. Yield StreamChunk satu per satu.
        Chunk terakhir punya is_done=True dan usage terisi.
        Pakai ini untuk: chat interface, response panjang.
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Cek apakah provider ini available (API key valid, endpoint reachable).
        Dipakai oleh fallback chain di gateway.
        """
        ...

    def _estimate_cost(self, tokens_input: int, tokens_output: int) -> float:
        """
        Override di subclass jika mau cost tracking per-provider.
        Default: return 0 (Ollama lokal = gratis).
        """
        return 0.0