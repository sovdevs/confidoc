"""Global strictness escalation table + per-profile override resolution.

Strictness modifies the base profile rules but never blindly destroys
task usefulness. Profiles remain authoritative; the global table is only
the default escalation path.

Escalation direction: minimal → balanced → strict → maximum
"""

from __future__ import annotations

from app.policy_engine.profiles import ProfileConfig

# ── Global escalation table ──────────────────────────────────────────────────
# {strictness_level: {base_action: escalated_action}}
# Actions not listed keep their base value.

GLOBAL_ESCALATION: dict[str, dict[str, str]] = {
    "minimal": {
        # No changes — minimal is the most permissive level
    },
    "balanced": {
        # Profile default rules apply unchanged
    },
    "strict": {
        "keep":           "flag_for_review",   # quasi-identifiers reviewed
        "generalize":     "generalize",        # already generalized, no change
        "relative_date":  "relative_date",     # stays as relative
        "stable_token":   "stable_token",      # identity-critical placeholders preserved
        "remove":         "remove",
        "flag_for_review":"flag_for_review",
    },
    "maximum": {
        "keep":            "flag_for_review",
        "stable_token":    "remove",           # overridden by rehydration_required
        "flag_for_review": "remove",
        "generalize":      "remove",
        "relative_date":   "coarse_relative_date",
        "remove":          "remove",
        "age_from_dob":    "age_band_from_dob",
    },
}

# Provider risk → minimum effective strictness
PROVIDER_RISK_FLOOR: dict[str, str] = {
    "trusted_internal": "minimal",
    "trusted_vendor":   "balanced",
    "local_llm":        "balanced",
    "cloud_llm":        "strict",
    "external_researcher": "strict",
    "public":           "maximum",
}

STRICTNESS_ORDER = ["minimal", "balanced", "strict", "maximum"]


def effective_strictness(requested: str, provider_risk: str) -> str:
    """Return the strictness level that satisfies both the request and provider floor."""
    floor = PROVIDER_RISK_FLOOR.get(provider_risk, "balanced")
    req_idx   = STRICTNESS_ORDER.index(requested)   if requested in STRICTNESS_ORDER else 1
    floor_idx = STRICTNESS_ORDER.index(floor)       if floor in STRICTNESS_ORDER else 0
    return STRICTNESS_ORDER[max(req_idx, floor_idx)]


def resolve_action(
    base_action: str,
    strictness: str,
    profile: ProfileConfig,
    entity_label: str,
) -> str:
    """Return the final action after applying global escalation + profile overrides."""
    if strictness not in STRICTNESS_ORDER:
        return base_action

    # 1. Apply global escalation
    escalated = GLOBAL_ESCALATION.get(strictness, {}).get(base_action, base_action)

    # 2. Apply per-profile strictness override for this level
    profile_level_overrides = profile.strictness_overrides.get(strictness, {})
    escalated = profile_level_overrides.get(entity_label,
                    profile_level_overrides.get(base_action, escalated))

    # 3. Respect rehydration_required: at maximum, preserve stable_token
    if (strictness == "maximum"
            and profile.rehydration_required
            and base_action == "stable_token"):
        escalated = "stable_token"

    return escalated


def strictness_index(level: str) -> int:
    return STRICTNESS_ORDER.index(level) if level in STRICTNESS_ORDER else 1
