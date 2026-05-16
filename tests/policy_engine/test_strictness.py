"""Tests: strictness escalation, provider risk floor, profile overrides."""

from app.policy_engine.profiles import load_profile, ProfileConfig, EntityRule
from app.policy_engine.strictness import (
    effective_strictness, resolve_action, PROVIDER_RISK_FLOOR, STRICTNESS_ORDER
)


def test_strict_escalates_keep_to_flag_for_review():
    profile = load_profile("translation")
    result = resolve_action("keep", "strict", profile, "SOME_FIELD")
    assert result == "flag_for_review"


def test_maximum_escalates_stable_token_to_remove_when_not_rehydration():
    profile = load_profile("research_extract")
    assert profile.rehydration_required is False
    result = resolve_action("stable_token", "maximum", profile, "PATIENT_NAME")
    assert result == "remove"


def test_maximum_preserves_stable_token_when_rehydration_required():
    profile = load_profile("translation")
    assert profile.rehydration_required is True
    result = resolve_action("stable_token", "maximum", profile, "PATIENT_NAME")
    assert result == "stable_token"


def test_balanced_keeps_profile_defaults():
    profile = load_profile("translation")
    # balanced: no changes from profile defaults
    result = resolve_action("stable_token", "balanced", profile, "PATIENT_NAME")
    assert result == "stable_token"
    result = resolve_action("remove", "balanced", profile, "ADDRESS")
    assert result == "remove"


def test_minimal_no_escalation():
    profile = load_profile("translation")
    for base in ["keep", "stable_token", "remove", "generalize"]:
        result = resolve_action(base, "minimal", profile, "TEST")
        assert result == base


def test_cloud_llm_floors_at_strict():
    level = effective_strictness("balanced", "cloud_llm")
    assert level == "strict"


def test_public_floors_at_maximum():
    level = effective_strictness("balanced", "public")
    assert level == "maximum"


def test_trusted_internal_allows_minimal():
    level = effective_strictness("minimal", "trusted_internal")
    assert level == "minimal"


def test_effective_strictness_takes_higher_of_request_and_floor():
    # Request strict + cloud_llm floor=strict → strict
    assert effective_strictness("strict", "cloud_llm") == "strict"
    # Request minimal + cloud_llm floor=strict → strict (floor wins)
    assert effective_strictness("minimal", "cloud_llm") == "strict"
    # Request maximum + trusted_internal floor=minimal → maximum (request wins)
    assert effective_strictness("maximum", "trusted_internal") == "maximum"


def test_strict_removes_less_than_maximum():
    profile = load_profile("research_extract")
    # At strict, flag_for_review stays as flag_for_review
    strict_result = resolve_action("flag_for_review", "strict", profile, "RARE_CONDITION")
    # At maximum, flag_for_review escalates to remove
    max_result = resolve_action("flag_for_review", "maximum", profile, "RARE_CONDITION")
    assert max_result == "remove"
    assert strict_result == "flag_for_review"
