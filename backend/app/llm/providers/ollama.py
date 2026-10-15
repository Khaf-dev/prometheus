"""
Ollama local provider implementation.
Optimal untuk: quick tasks, sensitive data (never leaves machine), cost = $0.
Requires Ollama running locally: https://ollama.ai
"""

from typing import AsyncIterator

import httpx

from app.llm.base import (
    BaseLLMProvider,
    LLMConfig,
    LLMResponse,
    Message,
    ProviderName,
    StreamChunk,
)

DEFAULT_MODEL = "qwen2.5:14b"   # balance antara speed dan quality
DEFAULT_BASE_URL = "http://localhost:11434"


class OllamaProvider(BaseLLMProvider):
    """
    Ollama pakai OpenAI-compatible API (/api/chat endpoint).
    Tidak perlu API key — jalan 100% lokal.
    Cost = $0, privacy = maximum.
    """

    def __init__(
        self,
        api_key: str = "",                  # unused, kept for interface consistency
        default_model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
    ):
        super().__init__(api_key, default_model)
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(120.0),   # model lokal bisa lebih lambat
        )

    @property
    def provider_name(self) -> ProviderName:
        return ProviderName.OLLAMA

    async def complete(
        self,
        messages: list[Message],
        config: LLMConfig,
    ) -> LLMResponse:
        model = config.model or self.default_model
        payload = {
            "model": model,
            "messages": self._convert_messages(messages, config.system_prompt),
            "stream": False,
            "options": {
                "temperature": config.temperature,
                "num_predict": config.max_tokens,
            },
        }

        response = await self._client.post("/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()

        content = data["message"]["content"]
        tokens_in = data.get("prompt_eval_count", 0)
        tokens_out = data.get("eval_count", 0)

        return LLMResponse(
            content=content,
            provider=self.provider_name,
            model=model,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            cost_usd=0.0,   # lokal = gratis
        )

    async def stream(
        self,
        messages: list[Message],
        config: LLMConfig,
    ) -> AsyncIterator[StreamChunk]:
        model = config.model or self.default_model
        payload = {
            "model": model,
            "messages": self._convert_messages(messages, config.system_prompt),
            "stream": True,
            "options": {
                "temperature": config.temperature,
                "num_predict": config.max_tokens,
            },
        }

        tokens_in = 0
        tokens_out = 0

        async with self._client.stream("POST", "/api/chat", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue

                import json
                data = json.loads(line)
                delta = data.get("message", {}).get("content", "")
                done = data.get("done", False)

                if delta:
                    yield StreamChunk(delta=delta, provider=self.provider_name)

                if done:
                    tokens_in = data.get("prompt_eval_count", 0)
                    tokens_out = data.get("eval_count", 0)
                    yield StreamChunk(
                        delta="",
                        provider=self.provider_name,
                        is_done=True,
                        usage={
                            "tokens_input": tokens_in,
                            "tokens_output": tokens_out,
                            "cost_usd": 0.0,
                            "model": model,
                        },
                    )

    async def health_check(self) -> bool:
        """Cek apakah Ollama service lagi running di lokal."""
        try:
            response = await self._client.get("/api/tags", timeout=5.0)
            return response.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        """List semua model yang udah di-pull di Ollama lokal."""
        try:
            response = await self._client.get("/api/tags")
            data = response.json()
            return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []

    @staticmethod
    def _convert_messages(
        messages: list[Message],
        system_prompt: str | None,
    ) -> list[dict]:
        result = []
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        result.extend(
            {"role": msg.role, "content": msg.content}
            for msg in messages
            if msg.role != "system"
        )
        return result