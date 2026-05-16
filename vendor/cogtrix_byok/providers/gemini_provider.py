from __future__ import annotations
from typing import AsyncGenerator

from cogtrix_byok.models import LLMRequest, LLMResponse
from cogtrix_byok.providers.base import BaseLLMProvider


class GeminiProvider(BaseLLMProvider):
    provider_name = "gemini"

    async def complete(self, request: LLMRequest, api_key: str) -> LLMResponse:
        raise NotImplementedError("Gemini provider not yet implemented")

    async def stream_complete(
        self, request: LLMRequest, api_key: str
    ) -> AsyncGenerator[str, None]:
        raise NotImplementedError("Gemini provider not yet implemented")
        yield  # make it a generator

    async def list_models(self, api_key: str) -> list[str]:
        raise NotImplementedError("Gemini provider not yet implemented")
