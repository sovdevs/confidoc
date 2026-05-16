"""Load and validate policy profiles from YAML or JSON files.

A profile defines:
  - entity_rules: what to do with each entity label at the default strictness
  - strictness_overrides: per-level action escalations specific to this profile
  - usefulness: threshold + weighted checks for this task
  - rehydration_required: whether stable tokens must be preserved at maximum strictness
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

PROFILES_DIR = Path(__file__).parent.parent.parent / "profiles"


class ProfileError(Exception):
    pass


@dataclass
class EntityRule:
    action: str                          # keep|remove|stable_token|generalize|age_from_dob|...
    token_type: Optional[str] = None     # e.g. PATIENT → [PATIENT_001]
    flag: Optional[str] = None           # e.g. reidentification_risk
    relative_date_mode: str = "token"    # token | offset
    relative_date_anchor: str = "REPORT_DATE"
    location_mode: str = "exact_to_placeholder"  # city_to_region|city_to_country|...


@dataclass
class UsefulnessConfig:
    threshold: float = 0.75
    weights: dict[str, float] = field(default_factory=dict)


@dataclass
class ProfileConfig:
    name: str
    description: str = ""
    default_strictness: str = "balanced"
    rehydration_required: bool = False
    entity_rules: dict[str, EntityRule] = field(default_factory=dict)
    strictness_overrides: dict[str, dict[str, str]] = field(default_factory=dict)
    usefulness: UsefulnessConfig = field(default_factory=UsefulnessConfig)

    def rule_for(self, label: str) -> EntityRule:
        """Return the entity rule for a label, defaulting to keep if not defined."""
        return self.entity_rules.get(label, EntityRule(action="keep"))


def _parse_entity_rule(data: Any) -> EntityRule:
    if isinstance(data, str):
        return EntityRule(action=data)
    r = EntityRule(action=data.get("action", "keep"))
    r.token_type = data.get("token_type")
    r.flag = data.get("flag")
    if "relative_date" in data:
        rd = data["relative_date"]
        r.relative_date_mode = rd.get("mode", "token")
        r.relative_date_anchor = rd.get("anchor", "REPORT_DATE")
    r.location_mode = data.get("location_mode", "exact_to_placeholder")
    return r


def _parse_usefulness(data: Any) -> UsefulnessConfig:
    if not data:
        return UsefulnessConfig()
    return UsefulnessConfig(
        threshold=float(data.get("threshold", 0.75)),
        weights={k: float(v) for k, v in data.get("weights", {}).items()},
    )


def _load_raw(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        return yaml.safe_load(text)
    return json.loads(text)


def load_profile(name_or_path: str | Path) -> ProfileConfig:
    """Load a profile by name (looks in profiles/) or by explicit path."""
    path = Path(name_or_path)
    if not path.suffix:
        # Try yaml then json
        for ext in (".yaml", ".yml", ".json"):
            candidate = PROFILES_DIR / f"{name_or_path}{ext}"
            if candidate.exists():
                path = candidate
                break
        else:
            raise ProfileError(f"Profile '{name_or_path}' not found in {PROFILES_DIR}")
    if not path.exists():
        raise ProfileError(f"Profile file not found: {path}")

    try:
        raw = _load_raw(path)
    except Exception as e:
        raise ProfileError(f"Failed to parse profile {path}: {e}") from e

    if "profile" not in raw:
        raise ProfileError(f"Profile {path} missing required 'profile' key")

    entity_rules = {
        label: _parse_entity_rule(rule_data)
        for label, rule_data in raw.get("entity_rules", {}).items()
    }

    strictness_overrides: dict[str, dict[str, str]] = {}
    for level, overrides in raw.get("strictness_overrides", {}).items():
        strictness_overrides[level] = {k: str(v) for k, v in overrides.items()}

    return ProfileConfig(
        name=raw["profile"],
        description=raw.get("description", ""),
        default_strictness=raw.get("default_strictness", "balanced"),
        rehydration_required=bool(raw.get("rehydration_required", False)),
        entity_rules=entity_rules,
        strictness_overrides=strictness_overrides,
        usefulness=_parse_usefulness(raw.get("usefulness")),
    )


def list_profiles() -> list[str]:
    return [p.stem for p in PROFILES_DIR.glob("*.yaml")] + \
           [p.stem for p in PROFILES_DIR.glob("*.json")]
