"""LLM adapter for Confidoc — thin wrapper over cogtrix_byok.

Two profiles are configured at startup via env vars (see app/config.py):
  - pdf:  vision-capable model for scanned PDF page extraction
  - anon: text model for LLM-assisted PII detection

For registered BYOK providers (openrouter, openai) BYOKService handles dispatch.
For localhost and google, LocalhostProvider is instantiated directly.
"""

from __future__ import annotations
from typing import Any

from cogtrix_byok import BYOKService, InMemoryKeyStore, LLMRequest, ProviderName
from cogtrix_byok.providers.localhost_provider import LocalhostProvider

from app.config import settings

_GOOGLE_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"

# Providers that route through BYOKService (ProviderName enum members)
_BYOK_PROVIDERS = {"openrouter", "openai", "anthropic"}


def _make_service(provider: str, api_key: str) -> BYOKService:
    return BYOKService(
        key_store=InMemoryKeyStore(),
        default_provider=ProviderName(provider),
        default_api_key=api_key,
    )


def _make_service_if_byok(provider: str, api_key: str) -> BYOKService | None:
    """Return a BYOKService only for registered providers; None for google/localhost."""
    if provider in _BYOK_PROVIDERS:
        return _make_service(provider, api_key)
    return None


# Module-level cached services — None when provider is google/localhost
_pdf_service  = _make_service_if_byok(settings.pdf_provider,  settings.pdf_api_key)
_anon_service = _make_service_if_byok(settings.anon_provider, settings.anon_api_key)


def _localhost_provider(base_url: str) -> LocalhostProvider:
    return LocalhostProvider(base_url=base_url or "http://localhost:1234/v1")


def _google_provider(base_url: str) -> LocalhostProvider:
    return LocalhostProvider(base_url=base_url or _GOOGLE_BASE)


async def pdf_complete_vision(
    images: list[bytes],
    text_prompt: str,
    system: str,
    override_provider: str | None = None,
    override_model: str | None = None,
    override_api_key: str | None = None,
) -> str:
    """Send page images to the configured (or overridden) PDF extraction model."""
    provider = override_provider or settings.pdf_provider
    model    = override_model    or settings.pdf_model
    api_key  = override_api_key  or settings.pdf_api_key

    request = LLMRequest.from_vision(
        text=text_prompt,
        images=images,
        model=model,
        temperature=0.0,
        system=system,
    )

    if provider == "localhost":
        resp = await _localhost_provider(settings.pdf_base_url).complete(request, api_key=api_key or "none")
    elif provider == "google":
        resp = await _google_provider(settings.pdf_base_url).complete(request, api_key=api_key or "none")
    elif override_provider or override_model or override_api_key:
        resp = await _make_service(provider, api_key).complete(request)
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
        resp = await _localhost_provider(settings.anon_base_url).complete(request, api_key=settings.anon_api_key or "none")
    elif settings.anon_provider == "google":
        resp = await _google_provider(settings.anon_base_url).complete(request, api_key=settings.anon_api_key or "none")
    else:
        resp = await _anon_service.complete(request)

    return resp.content


async def llm_export_complete(
    prompt: str,
    document: str,
    provider: str,
    model: str,
    api_key: str,
) -> str:
    """Run a user-facing LLM export task (summary, translation, QA, etc.).

    The document must already be PII-public (pseudonymized) markdown.
    No hardcoded system prompt — the caller supplies the full prompt.
    """
    messages = [
        {"role": "user", "content": f"{prompt}\n\nDocument:\n{document}"},
    ]
    request = LLMRequest(
        messages=messages,
        model=model,
        temperature=0.0,
    )

    if provider == "localhost":
        resp = await _localhost_provider("").complete(request, api_key=api_key or "none")
    elif provider == "google":
        resp = await _google_provider("").complete(request, api_key=api_key or "none")
    else:
        resp = await _make_service(provider, api_key).complete(request)

    return resp.content
