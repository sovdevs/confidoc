"""Tests: risk detection, warnings, scoring."""

from app.policy_engine.risk import score_risk

CLEAN = """
# Bericht

[PATIENT_001], age band 50-59, presented for examination.
Diagnose: Uterusmyomatosus. Ferrosanol 100 mg. Blutdruck 118/76 mmHg.
Day +0. Kontrolle empfohlen.
"""

WITH_NAME = "Maria Schmidt, age 51. Diagnose: Uterusmyomatosus."
WITH_ADDRESS = "Lindenweg 12, 65189 Wiesbaden. Diagnose: Myom."
WITH_EMAIL = "Contact dr.brandt@klinik.de for results."
WITH_PHONE = "Tel: +49 611 123456. Diagnose: Myom."
WITH_DOB = "Geburtsdatum: 08.07.1972. Diagnose: Myom."
WITH_RARE = "Diagnose: Epilepsie. Medikation: Levetiracetam."


def test_clean_doc_low_risk():
    result = score_risk(CLEAN, [])
    assert result.risk_score == "low"
    assert result.direct_identifier_risk == "low"


def test_name_present_triggers_warning():
    result = score_risk(WITH_NAME, [])
    assert not result.checks["no_full_names"]
    assert any("name" in w.lower() for w in result.warnings)


def test_address_triggers_warning():
    result = score_risk(WITH_ADDRESS, [])
    assert not result.checks["no_addresses"]
    assert any("address" in w.lower() for w in result.warnings)


def test_email_triggers_warning():
    result = score_risk(WITH_EMAIL, [])
    assert not result.checks["no_emails"]


def test_phone_triggers_warning():
    result = score_risk(WITH_PHONE, [])
    assert not result.checks["no_phone_numbers"]


def test_exact_dob_triggers_warning():
    result = score_risk(WITH_DOB, [])
    assert not result.checks["no_exact_dob"]
    assert any("date" in w.lower() or "dob" in w.lower() for w in result.warnings)


def test_rare_condition_quasi_identifier_warning():
    result = score_risk(WITH_RARE, [])
    assert not result.checks["rare_condition_clear"]
    assert any("rare" in w.lower() or "quasi" in w.lower() or "epilep" in w.lower()
               for w in result.warnings)


def test_high_risk_doc():
    doc = "Maria Schmidt, 08.07.1972, Lindenweg 12. Tel: +49 611 123."
    result = score_risk(doc, [])
    assert result.risk_score in ("medium", "high")
    assert result.direct_identifier_risk in ("medium", "high")
