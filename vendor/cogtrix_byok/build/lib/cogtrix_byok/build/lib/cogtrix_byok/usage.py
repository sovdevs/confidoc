from __future__ import annotations

# Pricing per 1M tokens (USD) — approximate.
PRICING: dict[str, dict[str, dict[str, float]]] = {
    "openai": {
        "gpt-4o-mini":   {"input": 0.15,  "output": 0.60},
        "gpt-4o":        {"input": 2.50,  "output": 10.00},
        "gpt-4.1-mini":  {"input": 0.40,  "output": 1.60},
        "gpt-4.1":       {"input": 2.00,  "output": 8.00},
        "gpt-4-turbo":   {"input": 10.00, "output": 30.00},
        "gpt-4":         {"input": 30.00, "output": 60.00},
        "gpt-3.5-turbo": {"input": 0.50,  "output": 1.50},
    },
    "openrouter": {},   # dynamic — use fallback
}

_FALLBACK = {"input": 0.15, "output": 0.60}


def estimate_cost(
    provider: str, model: str, prompt_tokens: int, completion_tokens: int
) -> float:
    p = PRICING.get(provider, {}).get(model) or _FALLBACK
    return (prompt_tokens * p["input"] + completion_tokens * p["output"]) / 1_000_000
