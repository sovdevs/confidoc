from __future__ import annotations
from abc import ABC, abstractmethod
from typing import AsyncGenerator

from cogtrix_byok.models import LLMRequest, LLMResponse


class BaseLLMProvider(ABC):
    """All providers implement this interface."""

    provider_name: str = ""

    # True when the provider forwards messages verbatim and can therefore pass
    # OpenAI-compatible image_url content parts to a vision-capable model.
    # Consuming code is responsible for choosing a vision-capable model string.
    supports_vision: bool = False

    @abstractmethod
    async def complete(self, request: LLMRequest, api_key: str) -> LLMResponse:
        """Single-turn chat completion. Returns full response."""

    @abstractmethod
    async def stream_complete(
        self, request: LLMRequest, api_key: str
    ) -> AsyncGenerator[str, None]:
        """Token-streaming completion. Yields text deltas."""

    @abstractmethod
    async def list_models(self, api_key: str) -> list[str]:
        """Return available model IDs for this API key."""

    async def validate_key(self, api_key: str) -> tuple[bool, str, list[str]]:
        """Validate an API key. Returns (valid, error_message, model_list)."""
        try:
            models = await self.list_models(api_key)
            return True, "", models
        except Exception as exc:
            return False, str(exc), []
