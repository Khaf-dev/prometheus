"""
LLM Gateway — orchestrator utama Prometheus.

Responsibilities:
  1. Auto-routing berdasarkan TaskType
  2. Fallback chain jika provider down
  3. Cost tracking per-session
  4. Health check semua provider
  5. Single entry point untuk semua LLM calls

Usage:
    gateway = LLMGateway.from_env()

    # Auto-route (gateway pilihkan provider terbaik)
    async for chunk in gateway.stream(messages, LLMConfig(task_type=TaskType.CODING)):
        print(chunk.delta, end="", flush=True)

    # Manual override
    async for chunk in gateway.stream(messages, LLMConfig(provider=ProviderName.OLLAMA)):
        print(chunk.delta, end="", flush=True)
"""

import logging
import os
from typing import AsyncIterator

from app.llm.base import (
    BaseLLMProvider,
    LLMConfig,
    LLMResponse,
    Message,
    ProviderName,
    StreamChunk,
    TaskType,
)
from app.llm.providers.claude import ClaudeProvider
from app.llm.providers.gemini import GeminiProvider
from app.llm.providers.gpt4o import GPT4oProvider
from app.llm.providers.ollama import OllamaProvider

logger = logging.getLogger(__name__)

# Routing rules: TaskType → urutan provider yang dicoba (index 0 = primary)
# Jika primary down → fallback ke index berikutnya → dst
ROUTING_TABLE: dict[TaskType, list[ProviderName]] = {
    TaskType.CODING:        [ProviderName.CLAUDE, ProviderName.GPT40, ProviderName.OLLAMA],
    TaskType.RESEARCH:      [ProviderName.GEMINI, ProviderName.CLAUDE, ProviderName.OLLAMA],
    TaskType.QUICK:         [ProviderName.OLLAMA, ProviderName.CLAUDE],
    TaskType.GENERAL:       [ProviderName.CLAUDE, ProviderName.GPT40, ProviderName.GEMINI, ProviderName.OLLAMA],
}

class LLMGateway:
    """
    Single entry point untuk semua interaksi LLM di Prometheus.
    Inject ke FastAPI via dependency injection.
    """

    def __init__(self, providers: dict[ProviderName, BaseLLMProvider]):
        self._providers = providers
        self._session_cost: float = 0.0


    # -------------------------------------------------------------------------
    # Factory
    # -------------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> "LLMGateway":
        """
        Buat gateway dari environment variables.
        Provider yang API key-nya kosong/tidak ada → di-skip (tidak di-register).
        Ini memastikan Ollama selalu available walau semua cloud key absent.
        """
        providers: dict[ProviderName, BaseLLMProvider] = {}

        if key := os.getenv("ANTHROPIC_API_KEY"):
            providers[ProviderName.CLAUDE] = ClaudeProvider(api_key=key)
            logger.info("Provider registered: Claude")

        if key := os.getenv("OPENAI_API_KEY"):
            providers[ProviderName.GPT40] = GPT4oProvider(api_key=key)
            logger.info("Provider registered: GPT-4o")

        if key := os.getenv("GOOGLE_API_KEY"):
            providers[ProviderName.GEMINI] = GeminiProvider(api_key=key)
            logger.info("Provider registered: Gemini")

        # Ollama selalu di-register (lokal, no key needed)
        ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        ollama_model = os.getenv("OLLAMA_DEFAULT_MODEL", "qwen2.5:14b")
        providers[ProviderName.OLLAMA] = OllamaProvider(
            base_url=ollama_url,
            default_model=ollama_model,
        )
        logger.info(f"Provider registered: Ollama ({ollama_url})")

        if not providers:
            raise RuntimeError(
                "Tidak ada LLM provider yang ter-register.  "
                "Set minimal satu API key di environment variables."
            )

        return cls(providers=providers)

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    async def complete(
        self,
        messages: list[Message],
        config: LLMConfig | None = None,
    ) -> LLMResponse:
        """Non-streaming completion dengan auto-fallback."""
        config = config or LLMConfig()
        provider = await self._resolve_provider(config)

        try:
            response = await provider.complete(messages, config)
            self._session_cost += response.cost_usd
            return response
        except Exception as e:
            logger.warning(f"Provider {provider.provider_name} failed: {e}. Trying fallback...")
            return await self._complete_with_fallback(messages, config, skip=provider.provider_name)

    async def stream(
        self,
        messages: list[Message],
        config: LLMConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Streaming completion dengan auto-fallback."""
        config = config or LLMConfig()
        provider = await self._resolve_provider(config)

        try:
            async for chunk in provider.stream(messages, config):
                if chunk.is_done and chunk.usage:
                    self._session_cost += chunk.usage.get("cost_usd", 0.0)
                yield chunk
        except Exception as e:
            logger.warning(f"Provider {provider.provider_name} failed during stream: {e}. Falling back...")
            fallback = await self._get_fallback_provider(config, skip=provider.provider_name)
            if fallback:
                async for chunk in fallback.stream(messages, config):
                    if chunk.is_done and chunk.usage:
                        self._session_cost += chunk.usage.get("cost_usd", 0.0)
                        yield chunk
            else:
                raise RuntimeError("Semua provider gagal. Cek koneksi atau jalankan Ollama lokal.")

    async def health_check_all(self) -> dict[str, bool]:
        """Cek status semua provider yang ter-register."""
        results = {}
        for name, provider in self._providers.items():
            results[name.value] = await provider.health_check()
        return results

    @property
    def session_cost_usd(self) -> float:
        """Total estimasi biaya session ini dalam USD."""
        return round(self._session_cost, 6)

    def reset_session_cost(self) -> None:
        self._session_cost = 0.0

    def available_providers(self) -> list[ProviderName]:
        return list(self._providers.keys())


    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    async def _resolve_provider(self, config: LLMConfig) -> BaseLLMProvider:
        """
        Resolve provider yang akan dipakai:
        1. Jika config.provider diset manual → pakai itu (user override)
        2. Kalau tidak → auto-route berdasarkan task_type
        """

        if config.provider:
            # Manual override - user tau apa yang dia mau
            provider = self._providers.get(config.provider)
            if not provider:
                raise ValueError(
                    f"Provider  '{config.provider}' tidak ter-register.  "
                    f"Available:    {list(self._providers.keys())}"
                )
            return provider

        # Auto-routing
        chain = ROUTING_TABLE.get(config.task_type, ROUTING_TABLE[TaskType.GENERAL])
        for provider_name in chain:
            if provider := self._providers.get(provider_name):
                return provider


        # Last resort : provider pertaman yg ada
        return next(iter(self._providers.values()))


    async def _complete_with_fallback(
        self,
        messages: list[Message],
        config: LLMConfig,
        skip: ProviderName,
    ) -> LLMResponse:
        fallback = await self._get_fallback_provider(config, skip=skip)
        if not fallback:
            raise RuntimeError("Semua provider gagal.")
        response = await fallback.complete(messages, config)
        self._session_cost += response.cost_usd
        return response


    async def _get_fallback_provider(
        self,
        config: LLMConfig,
        skip: ProviderName,
    ) -> BaseLLMProvider | None:
        """Ambil provider fallback dari routing chain, skip yang sudah gagal."""
        chain = ROUTING_TABLE.get(config.task_type, ROUTING_TABLE[TaskType.GENERAL])
        for provider_name in chain:
            if provider_name == skip:
                continue
            if provider := self._providers.get(provider_name):
                healthy = await provider.health_check()
                if healthy:
                    logger.info(f"Fallback to: {provider_name}")
                    return provider
        return None