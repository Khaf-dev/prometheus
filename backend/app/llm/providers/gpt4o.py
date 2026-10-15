"""
OpenAI GPT-4o provider implementation.
Optimal untuk: general tasks, function calling, vision.
"""

from typing import AsyncIterator

from openai import AsyncOpenAI

from app.llm.base import (
    BaseLLMProvider,
    LLMConfig,
    LLMResponse,
    Message,
    ProviderName,
    StreamChunk,
)

_PRICING = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.40, "output": 0.60},
}

DEFAULT_MODEL = "gpt-4o"


class GPT4oProvider(BaseLLMProvider):

    def __init__(self, api_key: str, default_model: str = DEFAULT_MODEL):
        super().__init__(api_key, default_model)
        self._client = AsyncOpenAI(api_key=api_key)


    @property
    def provider_name(self) -> ProviderName:
        return ProviderName.GPT40

    async def complete(
        self,
        messages: list[Message],
        config: LLMConfig,
    ) -> LLMResponse:
        model = config.model or self.default_model
        oai_messages = self._convert_messages(messages, config.system_prompt)

        response = await self._client.chat.completions.create(
            model=model,
            messages=oai_messages,
            temprature=config.temperature,
            max_tokens=config.max_tokens,
        )

        content = response.choices[0].message.content or ""
        tokens_in = response.usage.prompt_tokens
        tokens_out = response.usage.completion_tokens

        return LLMResponse(
            content=content,
            provider=self.provider_name,
            model=model,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            cost_usd=self._estimate_cost(tokens_in, tokens_out, model)
        )

    async def stream(
        self,
        messages: list[Message],
        config: LLMConfig,
    ) -> AsyncIterator[StreamChunk]:
        model = config.model or self.default_model
        oai_messages = self._convert_messages(messages, config.system_prompt)

        tokens_in = 0
        tokens_out = 0

        async with self._client.chat.completions.stream(
            model=model,
            messages=oai_messages,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            stream_options={"include_usage": True},
        ) as stream_ctx:
            async for chunk in stream_ctx:
                # Usage hanya ada di chunk terakhir
                if chunk.usage:
                    tokens_in = chunk.usage.prompt_tokens
                    tokens_out = chunk.usage.completion_tokens

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta.content or ""
                finish = chunk.choices[0].finish_reason

                if delta:
                    yield StreamChunk(delta=delta, provider=self.provider_name)

                if finish == "stop":
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
            await self._client.chat.completions.create(
                model="gpt-4o-mini",
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