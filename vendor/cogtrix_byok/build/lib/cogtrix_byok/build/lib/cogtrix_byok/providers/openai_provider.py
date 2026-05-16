from __future__ import annotations
from typing import AsyncGenerator

from openai import AsyncOpenAI
from openai import AuthenticationError
from openai import RateLimitError as _OAIRateLimit
from openai import APIError

from cogtrix_byok.models import LLMRequest, LLMResponse, ProviderName
from cogtrix_byok.providers.base import BaseLLMProvider
from cogtrix_byok import errors

_CHAT_PREFIXES = ("gpt-4", "gpt-3.5-turbo", "o1", "o3", "o4")


def _filter_chat_models(raw: list) -> list[str]:
    return sorted(m.id for m in raw if any(m.id.startswith(p) for p in _CHAT_PREFIXES))


class OpenAIProvider(BaseLLMProvider):
    provider_name = "openai"
    supports_vision = True

    def _client(self, api_key: str) -> AsyncOpenAI:
        return AsyncOpenAI(api_key=api_key)

    async def complete(self, request: LLMRequest, api_key: str) -> LLMResponse:
        client = self._client(api_key)
        kwargs: dict = dict(
            model=request.model,
            messages=request.messages,
            temperature=request.temperature,
        )
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens
        if request.response_format is not None:
            kwargs["response_format"] = request.response_format
        try:
            resp = await client.chat.completions.create(**kwargs)
        except AuthenticationError as exc:
            raise errors.InvalidKeyError(str(exc)) from exc
        except _OAIRateLimit as exc:
            raise errors.RateLimitError(str(exc)) from exc
        except APIError as exc:
            raise errors.ProviderError(self.provider_name, str(exc), exc) from exc

        usage: dict[str, int] = {}
        if resp.usage:
            usage = {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
            }
        return LLMResponse(
            content=resp.choices[0].message.content or "",
            usage=usage,
            model=resp.model,
            provider=ProviderName.OPENAI,
        )

    async def stream_complete(
        self, request: LLMRequest, api_key: str
    ) -> AsyncGenerator[str, None]:
        client = self._client(api_key)
        kwargs: dict = dict(
            model=request.model,
            messages=request.messages,
            temperature=request.temperature,
            stream=True,
        )
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens
        try:
            async with await client.chat.completions.create(**kwargs) as stream:
                async for chunk in stream:
                    delta = chunk.choices[0].delta.content if chunk.choices else None
                    if delta:
                        yield delta
        except AuthenticationError as exc:
            raise errors.InvalidKeyError(str(exc)) from exc
        except _OAIRateLimit as exc:
            raise errors.RateLimitError(str(exc)) from exc
        except APIError as exc:
            raise errors.ProviderError(self.provider_name, str(exc), exc) from exc

    async def list_models(self, api_key: str) -> list[str]:
        client = self._client(api_key)
        try:
            result = await client.models.list()
            return _filter_chat_models(result.data)
        except AuthenticationError as exc:
            raise errors.InvalidKeyError(str(exc)) from exc
        except APIError as exc:
            raise errors.ProviderError(self.provider_name, str(exc), exc) from exc
