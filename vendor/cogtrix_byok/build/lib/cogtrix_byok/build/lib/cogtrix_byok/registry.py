from __future__ import annotations

from cogtrix_byok.models import ProviderName
from cogtrix_byok.providers.base import BaseLLMProvider
from cogtrix_byok.providers.openai_provider import OpenAIProvider
from cogtrix_byok.providers.openrouter_provider import OpenRouterProvider
from cogtrix_byok.providers.anthropic_provider import AnthropicProvider
from cogtrix_byok.providers.gemini_provider import GeminiProvider

_REGISTRY: dict[ProviderName, BaseLLMProvider] = {
    ProviderName.OPENAI:     OpenAIProvider(),
    ProviderName.OPENROUTER: OpenRouterProvider(),
    ProviderName.ANTHROPIC:  AnthropicProvider(),
    ProviderName.GEMINI:     GeminiProvider(),
}


def get_provider(provider: ProviderName) -> BaseLLMProvider:
    p = _REGISTRY.get(provider)
    if p is None:
        raise ValueError(f"Unknown provider: {provider!r}")
    return p
