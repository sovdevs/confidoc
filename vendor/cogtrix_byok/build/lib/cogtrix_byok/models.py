from __future__ import annotations
import base64
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ProviderName(str, Enum):
    OPENAI     = "openai"
    OPENROUTER = "openrouter"
    ANTHROPIC  = "anthropic"
    GEMINI     = "gemini"
    LOCALHOST  = "localhost"


@dataclass
class LLMRequest:
    # Messages follow the OpenAI chat format.
    # Text-only callers: list[dict[str, str]] — unchanged from v1.
    # Vision callers:    list[dict[str, Any]] — content is a list of parts.
    # Both are valid; the type is relaxed to Any to accommodate both without
    # breaking existing text-only callers (no runtime change, type annotation only).
    messages: list[dict[str, Any]]
    model: str
    temperature: float = 0.0
    max_tokens: int | None = None
    response_format: dict[str, Any] | None = None

    @classmethod
    def from_vision(
        cls,
        text: str,
        images: list[bytes],
        model: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        system: str | None = None,
    ) -> LLMRequest:
        """Build a multimodal request from raw image bytes (PNG recommended).

        Images are encoded as base64 data-URL parts compatible with the
        OpenAI vision API and OpenRouter's multimodal pass-through.
        The ``text`` argument becomes the user text part; ``system`` is
        prepended as a system message when provided.

        Existing text-only callers are unaffected — this is additive.
        """
        content: list[dict[str, Any]] = []
        if text:
            content.append({"type": "text", "text": text})
        for img_bytes in images:
            b64 = base64.b64encode(img_bytes).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            })

        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})

        return cls(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )


@dataclass
class LLMResponse:
    content: str
    usage: dict[str, int]        # {"prompt_tokens": n, "completion_tokens": n}
    model: str
    provider: ProviderName


@dataclass
class ProviderCredentials:
    provider: ProviderName
    api_key: str
    extra: dict[str, Any] = field(default_factory=dict)
