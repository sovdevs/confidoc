"""Tests: transformation actions, entity handling, token stability."""

import json
from pathlib import Path

import pytest
from app.storage.jobs import Entity
from app.policy_engine.models import PolicyRequest
from app.policy_engine.profiles import load_profile
from app.policy_engine.transformer import transform

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_DOC = (FIXTURES / "sample_document.md").read_text()
SAMPLE_ENTITIES_RAW = json.loads((FIXTURES / "sample_entities.json").read_text())


def _entities():
    return [Entity(**e) for e in SAMPLE_ENTITIES_RAW]


def _request(task="translation", strictness="balanced", entities=None):
    return PolicyRequest(
        job_id="TEST_JOB",
        task=task,
        strictness_mode=strictness,
        consumer_type="trusted_vendor",
        provider_risk="trusted_vendor",
        document_text=SAMPLE_DOC,
        entities=entities or _entities(),
        document_date="11.10.2023",
    )


# ── Action tests ─────────────────────────────────────────────────────────────

def test_patient_name_becomes_stable_token():
    profile = load_profile("translation")
    req = _request()
    text, log, token_map = transform(req, profile, "balanced")
    entry = next(e for e in log if e.label == "PATIENT_NAME")
    assert entry.output.startswith("[PATIENT_")
    assert "Maria Schmidt" not in text


def test_address_removed_for_translation():
    profile = load_profile("translation")
    req = _request()
    text, log, token_map = transform(req, profile, "balanced")
    entry = next(e for e in log if e.label == "ADDRESS")
    assert entry.action_applied == "remove"
    assert entry.output.startswith("[REMOVED_")


def test_dob_becomes_age_for_translation():
    profile = load_profile("translation")
    doc = "Geburtsdatum: 08.07.1972"
    req = _request(entities=[
        Entity(id="dob1", label="DOB", text="08.07.1972", start=14, end=24,
               replacement="[DOB]", approved=True, dismissed=False)
    ])
    req.document_text = doc
    text, log, _ = transform(req, profile, "balanced")
    entry = next(e for e in log if e.label == "DOB")
    assert entry.action_applied == "age_from_dob"
    assert "age" in entry.output.lower()
    assert "1972" not in text


def test_dob_becomes_age_band_for_research():
    profile = load_profile("research_extract")
    req = _request(entities=[
        Entity(id="dob1", label="DOB", text="08.07.1972", start=0, end=10,
               replacement="[DOB]", approved=True, dismissed=False)
    ])
    req.document_text = "08.07.1972"
    text, log, _ = transform(req, profile, "maximum")
    entry = next(e for e in log if e.label == "DOB")
    assert entry.action_applied == "age_band_from_dob"
    assert "age band" in entry.output.lower()
    assert "-" in entry.output  # e.g. "50-59"


def test_diagnosis_preserved_translation():
    profile = load_profile("translation")
    req = _request(entities=[
        Entity(id="dx1", label="DIAGNOSIS", text="Uterusmyomatosus",
               start=210, end=226, replacement="[DIAGNOSIS]",
               approved=True, dismissed=False)
    ])
    req.document_text = "Diagnose: Uterusmyomatosus (D25.9)"
    text, log, _ = transform(req, profile, "balanced")
    entry = next(e for e in log if e.label == "DIAGNOSIS")
    assert entry.action_applied == "keep"
    assert "Uterusmyomatosus" in text


def test_medication_preserved():
    profile = load_profile("translation")
    req = _request(entities=[
        Entity(id="med1", label="MEDICATION", text="Ferrosanol",
               start=450, end=460, replacement="[MEDICATION]",
               approved=True, dismissed=False)
    ])
    req.document_text = "Medikation: Ferrosanol 100 mg"
    text, log, _ = transform(req, profile, "balanced")
    assert "Ferrosanol" in text


def test_dismissed_entity_not_transformed():
    profile = load_profile("translation")
    entities = [
        Entity(id="ph1", label="PHONE", text="+49 611 123456",
               start=0, end=14, replacement="[PHONE]",
               approved=False, dismissed=True)
    ]
    req = _request(entities=entities)
    req.document_text = "+49 611 123456"
    text, log, _ = transform(req, profile, "balanced")
    # Dismissed → not in log
    assert not any(e.label == "PHONE" for e in log)
    assert "+49 611 123456" in text


def test_pending_entity_adds_warning():
    profile = load_profile("translation")
    entities = [
        Entity(id="p1", label="PATIENT_NAME", text="Unknown",
               start=0, end=7, replacement="[PATIENT_NAME]",
               approved=False, dismissed=False)
    ]
    req = _request(entities=entities)
    req.document_text = "Unknown patient"
    text, log, _ = transform(req, profile, "balanced")
    # Not transformed (pending)
    assert "Unknown" in text


def test_stable_tokens_are_consistent():
    """Same entity text → same token throughout the document."""
    profile = load_profile("translation")
    entities = [
        Entity(id="a", label="PATIENT_NAME", text="Maria Schmidt",
               start=0, end=13, replacement="[PATIENT_NAME]",
               approved=True, dismissed=False),
        Entity(id="b", label="PATIENT_NAME", text="Maria Schmidt",
               start=50, end=63, replacement="[PATIENT_NAME]",
               approved=True, dismissed=False),
    ]
    req = _request(entities=entities)
    req.document_text = "Maria Schmidt ist Patient. Maria Schmidt wurde behandelt."
    text, log, token_map = transform(req, profile, "balanced")
    tokens = [e.output for e in log if e.label == "PATIENT_NAME"]
    assert tokens[0] == tokens[1], "Same name should produce same stable token"


def test_direct_identifiers_removed_from_prepared_output():
    profile = load_profile("research_extract")
    doc = "Patient: Maria Schmidt. Arzt: Dr. Klaus Brandt."
    entities = [
        Entity(id="p1", label="PATIENT_NAME", text="Maria Schmidt",
               start=9, end=22, replacement="[PATIENT_NAME]",
               approved=True, dismissed=False),
        Entity(id="p2", label="PHYSICIAN_NAME", text="Dr. Klaus Brandt",
               start=30, end=46, replacement="[PHYSICIAN_NAME]",
               approved=True, dismissed=False),
    ]
    req = _request(task="research_extract", entities=entities)
    req.document_text = doc
    text, log, _ = transform(req, profile, "maximum")
    assert "Maria Schmidt" not in text
    assert "Dr. Klaus Brandt" not in text


def test_location_generalized_for_translation():
    profile = load_profile("translation")
    entities = [
        Entity(id="loc1", label="LOCATION", text="Wiesbaden",
               start=0, end=9, replacement="[LOCATION]",
               approved=True, dismissed=False)
    ]
    req = _request(entities=entities)
    req.document_text = "Wiesbaden"
    text, log, _ = transform(req, profile, "balanced")
    entry = next(e for e in log if e.label == "LOCATION")
    assert entry.action_applied == "generalize"
    assert "Wiesbaden" not in text


def test_age_calculation_uses_report_date():
    """DOB 08.07.1972, report date 11.10.2023 → age 51."""
    profile = load_profile("translation")
    entities = [
        Entity(id="rpt", label="REPORT_DATE", text="11.10.2023",
               start=0, end=10, replacement="[DATE]",
               approved=True, dismissed=False),
        Entity(id="dob", label="DOB", text="08.07.1972",
               start=11, end=21, replacement="[DOB]",
               approved=True, dismissed=False),
    ]
    req = _request(entities=entities)
    req.document_text = "11.10.2023 08.07.1972"
    req.document_date = None  # force use of REPORT_DATE entity
    text, log, _ = transform(req, profile, "balanced")
    age_entry = next(e for e in log if e.label == "DOB")
    assert "51" in age_entry.output
