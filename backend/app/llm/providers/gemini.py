"""
Google Gemini provider implementation.
Pakai SDK terbaru: google-genai (bukan google-generativeai yang deprecated).
Optimal untuk: web research (1M token context), long document analysis.
"""

from typing import AsyncIterator

from google import genai
from google.genai import types

from app.llm.base import (
    BaseLLMProvider,
    LLMConfig,
    LLMResponse,
    Message,
    ProviderName,
    StreamChunk,
)

_PRICING = {
    "gemini-2.0-flash":       {"input": 0.10, "output": 0.40},
    "gemini-2.5-pro-preview": {"input": 1.25, "output": 10.00},
}

DEFAULT_MODEL = "gemini-2.0-flash"


class GeminiProvider(BaseLLMProvider):

    def __init__(self, api_key: str, default_model: str = DEFAULT_MODEL):
        super().__init__(api_key, default_model)
        self._client = genai.Client(api_key=api_key)

    @property
    def provider_name(self) -> ProviderName:
        return ProviderName.GEMINI

    async def complete(
        self,
        messages: list[Message],
        config: LLMConfig,
    ) -> LLMResponse:
        model_name = config.model or self.default_model
        contents, system_instruction = self._convert_messages(messages, config.system_prompt)

        gen_config = types.GenerateContentConfig(
            temperature=config.temperature,
            max_output_tokens=config.max_tokens,
            system_instruction=system_instruction,
        )

        response = await self._client.aio.models.generate_content(
            model=model_name,
            contents=contents,
            config=gen_config,
        )

        content = response.text or ""
        tokens_in = response.usage_metadata.prompt_token_count or 0
        tokens_out = response.usage_metadata.candidates_token_count or 0

        return LLMResponse(
            content=content,
            provider=self.provider_name,
            model=model_name,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            cost_usd=self._estimate_cost(tokens_in, tokens_out, model_name),
        )

    async def stream(
        self,
        messages: list[Message],
        config: LLMConfig,
    ) -> AsyncIterator[StreamChunk]:
        model_name = config.model or self.default_model
        contents, system_instruction = self._convert_messages(messages, config.system_prompt)

        gen_config = types.GenerateContentConfig(
            temperature=config.temperature,
            max_output_tokens=config.max_tokens,
            system_instruction=system_instruction,
        )

        tokens_in = 0
        tokens_out = 0

        async for chunk in await self._client.aio.models.generate_content_stream(
            model=model_name,
            contents=contents,
            config=gen_config,
        ):
            if chunk.text:
                yield StreamChunk(delta=chunk.text, provider=self.provider_name)
            if chunk.usage_metadata:
                tokens_in = chunk.usage_metadata.prompt_token_count or 0
                tokens_out = chunk.usage_metadata.candidates_token_count or 0

        yield StreamChunk(
            delta="",
            provider=self.provider_name,
            is_done=True,
            usage={
                "tokens_input": tokens_in,
                "tokens_output": tokens_out,
                "cost_usd": self._estimate_cost(tokens_in, tokens_out, model_name),
                "model": model_name,
            },
        )

    async def health_check(self) -> bool:
        try:
            await self._client.aio.models.generate_content(
                model="gemini-2.0-flash",
                contents="ping",
                config=types.GenerateContentConfig(max_output_tokens=10),
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
    ) -> tuple[list[types.Content], str | None]:
        """
        Convert ke format google-genai SDK.
        System message di-handle terpisah via GenerateContentConfig.system_instruction.
        Gemini pakai role 'user' / 'model' (bukan 'assistant').
        """
        role_map = {"user": "user", "assistant": "model"}
        contents = [
            types.Content(
                role=role_map.get(msg.role, "user"),
                parts=[types.Part(text=msg.content)],
            )
            for msg in messages
            if msg.role != "system"
        ]
        return contents, system_prompt