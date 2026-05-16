from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class BYOKConfig:
    """App-level feature flags for BYOK behaviour."""
    allow_byok: bool = True
    allowed_providers: list[str] = field(
        default_factory=lambda: ["openai", "openrouter"]
    )
    require_subscription: bool = False
