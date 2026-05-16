"""Tests: profile loading, invalid profile, missing entity rule defaults."""

import pytest
from app.policy_engine.profiles import ProfileConfig, ProfileError, load_profile
from pathlib import Path

PROFILES_DIR = Path(__file__).parent.parent.parent / "profiles"


def test_load_translation_profile():
    p = load_profile("translation")
    assert p.name == "translation"
    assert p.default_strictness == "balanced"
    assert p.rehydration_required is True
    assert "PATIENT_NAME" in p.entity_rules
    assert p.entity_rules["PATIENT_NAME"].action == "stable_token"
    assert p.entity_rules["PATIENT_NAME"].token_type == "PATIENT"


def test_load_clinical_summary_profile():
    p = load_profile("clinical_summary")
    assert p.name == "clinical_summary"
    assert p.default_strictness == "strict"
    assert p.usefulness.threshold == 0.90
    assert "diagnoses_preserved" in p.usefulness.weights


def test_load_research_profile():
    p = load_profile("research_extract")
    assert p.entity_rules["PATIENT_NAME"].action == "remove"
    assert p.entity_rules["DOB"].action == "age_band_from_dob"
    assert p.usefulness.threshold == 0.60


def test_load_public_release_profile():
    p = load_profile("public_release")
    assert p.usefulness.threshold == 0.40
    assert p.entity_rules["DATE"].action == "remove"


def test_missing_profile_raises():
    with pytest.raises(ProfileError):
        load_profile("nonexistent_profile")


def test_invalid_yaml_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("profile: test\nentity_rules: [not: valid: yaml:")
    with pytest.raises(ProfileError):
        load_profile(bad)


def test_missing_profile_key_raises(tmp_path):
    bad = tmp_path / "no_key.yaml"
    bad.write_text("description: missing profile key\nentity_rules: {}")
    with pytest.raises(ProfileError):
        load_profile(bad)


def test_missing_entity_rule_defaults_to_keep():
    p = load_profile("translation")
    rule = p.rule_for("UNKNOWN_LABEL")
    assert rule.action == "keep"


def test_usefulness_threshold_from_profile():
    p = load_profile("translation")
    assert p.usefulness.threshold == 0.78

    p2 = load_profile("clinical_summary")
    assert p2.usefulness.threshold == 0.90
    assert p2.usefulness.threshold > p.usefulness.threshold


def test_usefulness_weights_loaded():
    p = load_profile("translation")
    assert "sentence_meaning_preserved" in p.usefulness.weights
    assert abs(sum(p.usefulness.weights.values()) - 1.0) < 0.01


def test_missing_threshold_defaults_to_075(tmp_path):
    minimal = tmp_path / "minimal_profile.yaml"
    minimal.write_text("profile: minimal_test\nentity_rules: {}")
    p = load_profile(minimal)
    assert p.usefulness.threshold == 0.75


def test_strictness_overrides_loaded():
    p = load_profile("translation")
    assert "maximum" in p.strictness_overrides
