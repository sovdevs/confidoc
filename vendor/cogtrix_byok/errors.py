from __future__ import annotations


class BYOKError(Exception):
    pass


class InvalidKeyError(BYOKError):
    pass


class ModelNotFoundError(BYOKError):
    pass


class RateLimitError(BYOKError):
    pass


class QuotaExceededError(BYOKError):
    pass


class ProviderError(BYOKError):
    def __init__(self, provider: str, message: str, original: Exception | None = None):
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.original = original
