from __future__ import annotations
from abc import ABC, abstractmethod

from cogtrix_byok.models import ProviderName


class AbstractKeyStore(ABC):
    @abstractmethod
    def set(self, session_id: str, provider: ProviderName, api_key: str) -> None:
        pass

    @abstractmethod
    def get(self, session_id: str, provider: ProviderName) -> str | None:
        pass

    @abstractmethod
    def delete(self, session_id: str) -> None:
        pass

    @abstractmethod
    def clear_all(self) -> None:
        pass


class InMemoryKeyStore(AbstractKeyStore):
    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def set(self, session_id: str, provider: ProviderName, api_key: str) -> None:
        self._store[(session_id, provider.value)] = api_key

    def get(self, session_id: str, provider: ProviderName) -> str | None:
        return self._store.get((session_id, provider.value))

    def delete(self, session_id: str) -> None:
        for k in [k for k in self._store if k[0] == session_id]:
            del self._store[k]

    def clear_all(self) -> None:
        self._store.clear()
