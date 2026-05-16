"""LLM adapter for Confidoc — thin wrapper over cogtrix_byok.

Two profiles are configured at startup via env vars (see app/config.py):
  - pdf:  vision-capable model for scanned PDF page extraction
  - anon: text model for LLM-assisted PII detection

For registered providers (openrouter, openai) BYOKService handles dispatch.
For localhost, LocalhostProvider is instantiated directly (not in the registry).

Existing text-only calls go through the same path — no special casing needed.
"""

from __future__ import annotations
from typing import Any

from cogtrix_byok import BYOKService, InMemoryKeyStore, LLMRequest, ProviderName
from cogtrix_byok.providers.localhost_provider import LocalhostProvider

from app.config import settings


def _make_service(provider: str, api_key: str) -> BYOKService:
    return BYOKService(
        key_store=InMemoryKeyStore(),
        default_provider=ProviderName(provider),
        default_api_key=api_key,
    )


_pdf_service  = _make_service(settings.pdf_provider,  settings.pdf_api_key)
_anon_service = _make_service(settings.anon_provider, settings.anon_api_key)


async def pdf_complete_vision(
    images: list[bytes],
    text_prompt: str,
    system: str,
) -> str:
    """Send page images to the configured PDF extraction model.

    Returns the raw markdown string produced by the model.
    """
    request = LLMRequest.from_vision(
        text=text_prompt,
        images=images,
        model=settings.pdf_model,
        temperature=0.0,
        system=system,
    )

    if settings.pdf_provider == "localhost":
        provider = LocalhostProvider(base_url=settings.pdf_base_url or "http://localhost:1234/v1")
        resp = await provider.complete(request, api_key=settings.pdf_api_key or "none")
    else:
        resp = await _pdf_service.complete(request)

    return resp.content


async def anon_complete(
    messages: list[dict[str, Any]],
    response_format: dict[str, Any] | None = None,
) -> str:
    """Run a PII detection / anonymization LLM call (text-only).

    Returns the raw response string; caller is responsible for JSON parsing.
    """
    request = LLMRequest(
        messages=messages,
        model=settings.anon_model,
        temperature=0.0,
        response_format=response_format,
    )

    if settings.anon_provider == "localhost":
        provider = LocalhostProvider(base_url=settings.anon_base_url or "http://localhost:1234/v1")
        resp = await provider.complete(request, api_key=settings.anon_api_key or "none")
    else:
        resp = await _anon_service.complete(request)

    return resp.content
