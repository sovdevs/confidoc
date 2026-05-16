from __future__ import annotations
from typing import AsyncGenerator, Optional

from cogtrix_byok.models import LLMRequest, LLMResponse, ProviderName
from cogtrix_byok.registry import get_provider
from cogtrix_byok.storage.key_store import AbstractKeyStore


class BYOKService:
    def __init__(
        self,
        key_store: AbstractKeyStore,
        default_provider: ProviderName = ProviderName.OPENAI,
        default_api_key: str = "",
    ) -> None:
        self._key_store = key_store
        self._default_provider = default_provider
        self._default_api_key = default_api_key

    def _resolve(
        self,
        session_id: Optional[str],
        api_key: Optional[str],
        provider: Optional[ProviderName],
    ) -> tuple[ProviderName, str]:
        p = provider or self._default_provider
        if api_key:
            return p, api_key
        if session_id:
            stored = self._key_store.get(session_id, p)
            if stored:
                return p, stored
        return p, self._default_api_key

    async def complete(
        self,
        request: LLMRequest,
        session_id: Optional[str] = None,
        api_key: Optional[str] = None,
        provider: Optional[ProviderName] = None,
    ) -> LLMResponse:
        p, key = self._resolve(session_id, api_key, provider)
        return await get_provider(p).complete(request, key)

    async def stream_complete(
        self,
        request: LLMRequest,
        session_id: Optional[str] = None,
        api_key: Optional[str] = None,
        provider: Optional[ProviderName] = None,
    ) -> AsyncGenerator[str, None]:
        p, key = self._resolve(session_id, api_key, provider)
        return get_provider(p).stream_complete(request, key)

    async def validate_key(
        self,
        api_key: str,
        provider: ProviderName = ProviderName.OPENAI,
    ) -> tuple[bool, str, list[str]]:
        return await get_provider(provider).validate_key(api_key)

    async def list_models(
        self,
        api_key: Optional[str] = None,
        provider: ProviderName = ProviderName.OPENAI,
    ) -> list[str]:
        key = api_key or self._default_api_key
        return await get_provider(provider).list_models(key)

    def store_key(self, session_id: str, provider: ProviderName, api_key: str) -> None:
        self._key_store.set(session_id, provider, api_key)

    def remove_session(self, session_id: str) -> None:
        self._key_store.delete(session_id)
