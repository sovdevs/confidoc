from __future__ import annotations
from typing import AsyncGenerator

from openai import AsyncOpenAI
from openai import AuthenticationError
from openai import RateLimitError as _OAIRateLimit
from openai import APIError

from cogtrix_byok.models import LLMRequest, LLMResponse, ProviderName
from cogtrix_byok.providers.base import BaseLLMProvider
from cogtrix_byok import errors


class LocalhostProvider(BaseLLMProvider):
    """
    Generic OpenAI-compatible localhost provider.
    Covers LM Studio, Ollama (v1 mode), vLLM, llama.cpp server, and any
    other server that speaks the OpenAI chat completions API.
    """

    provider_name = "localhost"
    supports_vision = True  # passes image parts through; depends on local model supporting them

    def __init__(self, base_url: str) -> None:
        # Normalise: strip trailing slash
        self._base_url = base_url.rstrip("/")

    def _client(self, api_key: str) -> AsyncOpenAI:
        # Most local servers accept any non-empty key; "none" is a safe default.
        return AsyncOpenAI(
            api_key=api_key or "none",
            base_url=self._base_url,
        )

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
                "prompt_tokens": resp.usage.prompt_tokens or 0,
                "completion_tokens": resp.usage.completion_tokens or 0,
            }
        return LLMResponse(
            content=resp.choices[0].message.content or "",
            usage=usage,
            model=resp.model or request.model,
            provider=ProviderName.LOCALHOST,
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
        """Probe the /models endpoint. Raises on connection failure."""
        client = self._client(api_key)
        result = await client.models.list()
        return sorted(m.id for m in result.data)

    async def validate_key(self, api_key: str) -> tuple[bool, str, list[str]]:
        """Override to surface connection errors with a helpful message."""
        try:
            models = await self.list_models(api_key)
            return True, "", models
        except Exception as exc:
            msg = str(exc)
            if "Connection" in msg or "Network" in msg or "connect" in msg.lower():
                msg = (
                    f"Cannot reach {self._base_url} — is the server running and "
                    "accepting connections on that address? "
                    "If running on the same machine try http://localhost:1234/v1 instead."
                )
            return False, msg, []
