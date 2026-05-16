# cogtrix_byok — LLM Provider Abstraction Layer

## What this module is

`cogtrix_byok` is a provider-neutral LLM abstraction layer for the Cogtrix product family.
Despite the name (a historical artifact), it handles **all** LLM provider selections — not just
bring-your-own-key flows. Server default keys, user-supplied keys, OpenRouter, and local
inference servers all flow through the same interface.

It is a **pure Python library** with no FastAPI dependency. It can be dropped into any
async Python backend.

---

## Directory layout

```
cogtrix_byok/
├── __init__.py              # Public re-exports
├── models.py                # LLMRequest, LLMResponse, ProviderName, ProviderCredentials
├── errors.py                # BYOKError hierarchy
├── config.py                # BYOKConfig (app-level feature flags)
├── usage.py                 # Pricing tables, estimate_cost()
├── registry.py              # Singleton provider instances, get_provider()
├── service.py               # BYOKService — key resolution + dispatch
├── providers/
│   ├── base.py              # BaseLLMProvider ABC
│   ├── openai_provider.py   # Full implementation
│   ├── openrouter_provider.py  # Full implementation (OpenAI-compat, base_url override)
│   ├── localhost_provider.py   # Full implementation (configurable base_url)
│   ├── anthropic_provider.py   # Stub — raises NotImplementedError
│   └── gemini_provider.py      # Stub — raises NotImplementedError
└── storage/
    ├── key_store.py         # AbstractKeyStore + InMemoryKeyStore
    └── __init__.py
```

---

## Core types (`models.py`)

```python
class ProviderName(str, Enum):
    OPENAI     = "openai"
    OPENROUTER = "openrouter"
    ANTHROPIC  = "anthropic"   # stub
    GEMINI     = "gemini"      # stub
    LOCALHOST  = "localhost"   # any OpenAI-compatible local server

@dataclass
class LLMRequest:
    messages: list[dict[str, str]]   # OpenAI message format
    model: str
    temperature: float = 0.0
    max_tokens: int | None = None
    response_format: dict | None = None   # e.g. {"type": "json_object"}

@dataclass
class LLMResponse:
    content: str
    usage: dict[str, int]    # {"prompt_tokens": n, "completion_tokens": n}
    model: str
    provider: ProviderName
```

---

## Provider interface (`providers/base.py`)

Every provider implements:

```python
class BaseLLMProvider(ABC):
    async def complete(self, request: LLMRequest, api_key: str) -> LLMResponse: ...
    async def stream_complete(self, request: LLMRequest, api_key: str) -> AsyncGenerator[str, None]: ...
    async def list_models(self, api_key: str) -> list[str]: ...
    async def validate_key(self, api_key: str) -> tuple[bool, str, list[str]]:
        # Default implementation calls list_models().
        # Returns (is_valid, error_message, model_list)
```

---

## Key store (`storage/key_store.py`)

```python
class AbstractKeyStore(ABC):
    def set(self, session_id: str, provider: ProviderName, api_key: str) -> None: ...
    def get(self, session_id: str, provider: ProviderName) -> str | None: ...
    def delete(self, session_id: str) -> None: ...
    def clear_all(self) -> None: ...

class InMemoryKeyStore(AbstractKeyStore):
    # Dict-backed. Keys lost on process restart — frontend must re-register.
```

---

## BYOKService (`service.py`)

The central dispatch object. Constructed once at app startup.

```python
service = BYOKService(
    key_store=InMemoryKeyStore(),
    default_provider=ProviderName.OPENAI,
    default_api_key="sk-...",          # server's own key; can be ""
)
```

**Key resolution order** (inside `_resolve(session_id, api_key, provider)`):
1. Explicit `api_key` argument — used as-is
2. `session_id` lookup in `key_store` for the given provider
3. `default_api_key` fallback

**Public methods:**

```python
await service.complete(request, session_id=None, api_key=None, provider=None) -> LLMResponse
await service.stream_complete(request, ...) -> AsyncGenerator[str, None]
await service.validate_key(api_key, provider=ProviderName.OPENAI) -> tuple[bool, str, list[str]]
await service.list_models(api_key=None, provider=ProviderName.OPENAI) -> list[str]
service.store_key(session_id, provider, api_key) -> None
service.remove_session(session_id) -> None
```

---

## Provider notes

### OpenAI (`openai_provider.py`)
- Uses `openai.AsyncOpenAI`
- `list_models()` filters to chat-capable prefixes: `gpt-4`, `gpt-3.5-turbo`, `o1`, `o3`, `o4`
- Maps `AuthenticationError` → `errors.InvalidKeyError`, `RateLimitError` → `errors.RateLimitError`

### OpenRouter (`openrouter_provider.py`)
- Same OpenAI SDK, `base_url="https://openrouter.ai/api/v1"`
- Keys start with `sk-or-`
- `list_models()` returns the full OpenRouter catalogue (hundreds of models)
- Validate via `/api/v1/auth/key`; model list from public `/api/v1/models`

### LocalhostProvider (`localhost_provider.py`)
- Constructed with `base_url` e.g. `"http://localhost:1234/v1"`
- `api_key` defaults to `"none"` — most local servers accept any non-empty string
- `list_models()` probes `{base_url}/models`; raises on failure (connection error surfaced to caller)
- Covers: LM Studio, Ollama (v1 mode), vLLM, llama.cpp server, any OpenAI-compatible server
- **Not in registry** — instantiated on demand with the caller-supplied `base_url`

### Anthropic / Gemini
- Stubs only — `complete()` raises `NotImplementedError`

---

## Errors (`errors.py`)

```python
BYOKError           # base
├── InvalidKeyError
├── ModelNotFoundError
├── RateLimitError
├── QuotaExceededError
└── ProviderError(provider: str, message: str, original: Exception)
```

---

## Installation (editable path dep, uv)

In the consuming project's `pyproject.toml`:

```toml
[project]
dependencies = [
    "cogtrix-byok",
    ...
]

[tool.uv.sources]
cogtrix-byok = { path = "../cogtrix_byok", editable = true }
```

Then:

```bash
uv sync
```

The path is relative to the consuming project's `pyproject.toml`. Adjust depth as needed.
On a remote server the package must be synced separately before `uv sync` runs.

---

## Wiring into a new project — recommended pattern

The cleanest integration is a thin **adapter module** (`byok_adapter.py`) in the consuming
app. SegFlow's adapter at `web-mvp/backend/app/services/byok_adapter.py` is the reference
implementation. Here is the minimal version:

```python
# myapp/services/llm_adapter.py
from __future__ import annotations
import json
from typing import Any

from cogtrix_byok import BYOKService, InMemoryKeyStore, LLMRequest, ProviderName

service = BYOKService(
    key_store=InMemoryKeyStore(),
    default_provider=ProviderName.OPENAI,
    default_api_key="",   # set from your config
)


async def complete(
    messages: list[dict[str, Any]],
    model: str,
    temperature: float = 0.0,
    api_key: str | None = None,
    response_format: dict | None = None,
    provider: str | None = None,   # "openai" | "openrouter" | "localhost"
) -> tuple[str, dict[str, int]]:
    """Returns (content, usage_dict). usage keys: prompt_tokens, completion_tokens."""

    if provider == "localhost":
        # LocalhostProvider is not in the registry — instantiate on demand.
        # Expects api_key to be JSON: {"base_url": "...", "api_key": "...", "model": "..."}
        from cogtrix_byok.providers.localhost_provider import LocalhostProvider
        cfg: dict = {}
        try:
            cfg = json.loads(api_key or "{}")
        except (json.JSONDecodeError, TypeError):
            pass
        base_url = cfg.get("base_url", "http://localhost:1234/v1")
        local_key = cfg.get("api_key", "none")
        # NOTE: caller should pass cfg["model"] as the model arg (or override here)
        req = LLMRequest(messages=messages, model=model, temperature=temperature,
                         response_format=response_format)
        resp = await LocalhostProvider(base_url=base_url).complete(req, api_key=local_key)
        return resp.content, resp.usage

    req = LLMRequest(messages=messages, model=model, temperature=temperature,
                     response_format=response_format)
    p = ProviderName(provider) if provider else None
    resp = await service.complete(req, api_key=api_key, provider=p)
    return resp.content, resp.usage


async def validate_key(api_key: str, provider: str = "openai") -> tuple[bool, str, list[str]]:
    return await service.validate_key(api_key, ProviderName(provider))


async def validate_localhost(base_url: str, api_key: str = "none") -> tuple[bool, str, list[str]]:
    from cogtrix_byok.providers.localhost_provider import LocalhostProvider
    return await LocalhostProvider(base_url=base_url).validate_key(api_key)


async def list_models(api_key: str | None = None, provider: str = "openai") -> list[str]:
    return await service.list_models(api_key=api_key, provider=ProviderName(provider))
```

Then every LLM call in the app goes through `llm_adapter.complete(...)`.

---

## Localhost provider — key encoding convention

Because `BYOKService` stores one string per session+provider, the localhost config
(base_url + api_key + model) is encoded as a JSON string in the `api_key` field:

```python
import json
encoded = json.dumps({
    "base_url": "http://localhost:1234/v1",
    "api_key": "none",
    "model": "llama-3",
})
# store `encoded` as the session's api_key with provider="localhost"
```

The adapter decodes this before constructing `LocalhostProvider`. Keep this convention
consistent between the backend store and the adapter.

---

## What is NOT in this module

- HTTP routing / FastAPI endpoints — belong in the consuming app
- Subscriber / tier gating — belongs in the consuming app
- Usage cost tracking against a database — belongs in the consuming app
- Frontend UI for provider selection — belongs in the consuming app
- Session management beyond in-memory key lookup — implement `AbstractKeyStore`

---

## Adding a new provider

1. Create `providers/myprovider.py` implementing `BaseLLMProvider`
2. Add `MYPROVIDER = "myprovider"` to `ProviderName` in `models.py`
3. Add an instance to `_REGISTRY` in `registry.py`
4. Export from `providers/__init__.py`
5. No changes needed to `BYOKService` or the adapter

---

## Current status

| Provider | complete() | stream_complete() | list_models() | validate_key() |
|---|---|---|---|---|
| OpenAI | ✓ | ✓ | ✓ | ✓ |
| OpenRouter | ✓ | ✓ | ✓ | ✓ |
| Localhost (generic) | ✓ | ✓ | ✓ | ✓ |
| Anthropic | stub | stub | stub | stub |
| Gemini | stub | stub | stub | stub |
