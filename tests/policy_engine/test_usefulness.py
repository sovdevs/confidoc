"""Tests: usefulness scoring, profile-specific thresholds, check behaviour."""

from app.policy_engine.profiles import load_profile
from app.policy_engine.usefulness import score_usefulness

ORIGINAL = """
# Gynäkologischer Befundbericht

Diagnose: Uterusmyomatosus (D25.9)
Medikation: Ferrosanol 100 mg

Blutdruck: 118/76 mmHg. Hb: 12,4 g/dl.
Datum: 11.10.2023

## Empfehlungen
Kontrolle in 6 Monaten.
"""

PREPARED_GOOD = """
# Gynäkologischer Befundbericht

Diagnose: Uterusmyomatosus (D25.9)
Medikation: Ferrosanol 100 mg

Blutdruck: 118/76 mmHg. Hb: 12,4 g/dl.
Day +0

## Empfehlungen
Kontrolle in 6 Monaten.
"""

PREPARED_OVER_REDACTED = "# Bericht\n"

PREPARED_NO_DIAG = """
# Bericht

[PATIENT_001], age band 50-59.
Medikation: Ferrosanol 100 mg
Blutdruck: 118/76 mmHg
"""


def test_good_prepared_doc_passes_translation():
    profile = load_profile("translation")
    result = score_usefulness(ORIGINAL, PREPARED_GOOD, profile)
    assert result.passes
    assert result.score >= profile.usefulness.threshold


def test_over_redacted_fails():
    profile = load_profile("clinical_summary")
    result = score_usefulness(ORIGINAL, PREPARED_OVER_REDACTED, profile)
    assert not result.passes
    assert result.score < profile.usefulness.threshold


def test_diagnoses_preserved_check():
    profile = load_profile("clinical_summary")
    result = score_usefulness(ORIGINAL, PREPARED_GOOD, profile)
    assert result.checks.get("diagnoses_preserved") is True


def test_diagnoses_not_preserved_check():
    profile = load_profile("clinical_summary")
    result = score_usefulness(ORIGINAL, "# Bericht\n\nKeine Informationen.", profile)
    assert result.checks.get("diagnoses_preserved") is False


def test_measurements_preserved_check():
    profile = load_profile("clinical_summary")
    result = score_usefulness(ORIGINAL, PREPARED_GOOD, profile)
    assert result.checks.get("measurements_preserved") is True


def test_enough_text_remaining_fails_on_short_doc():
    profile = load_profile("clinical_summary")
    result = score_usefulness(ORIGINAL, "x", profile)
    assert result.checks.get("enough_text_remaining") is False


def test_clinical_summary_threshold_higher_than_translation():
    cs = load_profile("clinical_summary")
    tr = load_profile("translation")
    assert cs.usefulness.threshold > tr.usefulness.threshold


def test_research_threshold_lower_than_clinical():
    cs = load_profile("clinical_summary")
    rs = load_profile("research_extract")
    assert rs.usefulness.threshold < cs.usefulness.threshold


def test_score_clamped_0_to_1():
    profile = load_profile("translation")
    result = score_usefulness("", PREPARED_GOOD * 10, profile)
    assert 0.0 <= result.score <= 1.0
