"""
Claude (Anthropic) provider implementation.
Optimal untuk: coding tasks, complex reasoning, long context analysis.
"""

from typing import AsyncIterator

import anthropic

from app.llm.base import (
    BaseLLMProvider,
    LLMConfig,
    LLMResponse,
    Message,
    ProviderName,
    StreamChunk,
)

# Pricing per 1M tokens (Juni 2026) — update kalau ada perubahan
_PRICING = {
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-opus-4-5":   {"input": 15.00, "output": 75.00},
    "claude-haiku-4-5":  {"input": 0.80, "output": 4.00},
}

DEFAULT_MODEL = "claude-sonnet-4-5"


class ClaudeProvider(BaseLLMProvider):

    def __init__(self, api_key: str, default_model: str = DEFAULT_MODEL):
        super().__init__(api_key, default_model)
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    @property
    def provider_name(self) -> ProviderName:
        return ProviderName.CLAUDE

    async def complete(
        self,
        messages: list[Message],
        config: LLMConfig,
    ) -> LLMResponse:
        model = config.model or self.default_model
        anthropic_messages = self._convert_messages(messages)

        kwargs = dict(
            model=model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            messages=anthropic_messages,
        )
        if config.system_prompt:
            kwargs["system"] = config.system_prompt

        response = await self._client.messages.create(**kwargs)

        content = response.content[0].text
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens

        return LLMResponse(
            content=content,
            provider=self.provider_name,
            model=model,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            cost_usd=self._estimate_cost(tokens_in, tokens_out, model),
        )

    async def stream(
        self,
        messages: list[Message],
        config: LLMConfig,
    ) -> AsyncIterator[StreamChunk]:
        model = config.model or self.default_model
        anthropic_messages = self._convert_messages(messages)

        kwargs = dict(
            model=model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            messages=anthropic_messages,
        )
        if config.system_prompt:
            kwargs["system"] = config.system_prompt

        async with self._client.messages.stream(**kwargs) as stream_ctx:
            async for text in stream_ctx.text_stream:
                yield StreamChunk(
                    delta=text,
                    provider=self.provider_name,
                )

            # Final chunk dengan usage stats
            final = await stream_ctx.get_final_message()
            tokens_in = final.usage.input_tokens
            tokens_out = final.usage.output_tokens
            yield StreamChunk(
                delta="",
                provider=self.provider_name,
                is_done=True,
                usage={
                    "tokens_input": tokens_in,
                    "tokens_output": tokens_out,
                    "cost_usd": self._estimate_cost(tokens_in, tokens_out, model),
                    "model": model,
                },
            )

    async def health_check(self) -> bool:
        try:
            await self._client.messages.create(
                model="claude-haiku-4-5",  # model paling murah untuk health check
                max_tokens=10,
                messages=[{"role": "user", "content": "ping"}],
            )
            return True
        except Exception:
            return False

    def _estimate_cost(self, tokens_in: int, tokens_out: int, model: str) -> float:
        pricing = _PRICING.get(model, {"input": 0, "output": 0})
        cost = (tokens_in * pricing["input"] + tokens_out * pricing["output"]) / 1_000_000
        return round(cost, 6)

    @staticmethod
    def _convert_messages(messages: list[Message]) -> list[dict]:
        """
        Convert internal Message format ke format Anthropic API.
        System messages di-handle terpisah via system= parameter,
        jadi kita filter out di sini.
        """
        return [
            {"role": msg.role, "content": msg.content}
            for msg in messages
            if msg.role != "system"
        ]