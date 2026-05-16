"""Privacy guardrail: approved_terms.jsonl must never contain raw PHI.

Inserts a synthetic entity with a known fake name/address/phone/date,
triggers the learning logger, then asserts none of those raw strings appear
in the output file.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.pipeline.export import _log_approved_terms, _text_shape, _len_bucket
from app.storage.jobs import Entity, Job


# ── Synthetic PHI values — fake, obviously non-real ──────────────────────────
FAKE_NAME    = "Zylandro Questerfeld"
FAKE_ADDRESS = "Teststraße 999, 99999 Musterstadt"
FAKE_PHONE   = "+49 999 9999999"
FAKE_DATE    = "31.02.1900"
FAKE_IBAN    = "DE89370400440532013000"

RAW_PHI = [FAKE_NAME, FAKE_ADDRESS, FAKE_PHONE, FAKE_DATE, FAKE_IBAN]


def _make_entity(label: str, text: str) -> Entity:
    return Entity(
        label=label, text=text, start=0, end=len(text),
        replacement=f"[{label}]", approved=True, manual=True,
    )


@pytest.fixture()
def tmp_approved_terms(tmp_path):
    """Patch settings.approved_terms to a temp file for isolation."""
    p = tmp_path / "approved_terms.jsonl"
    p.touch()
    with patch("app.pipeline.export.settings") as mock_settings:
        mock_settings.approved_terms = p
        yield p


def _read_entries(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_no_raw_phi_in_learning_store(tmp_approved_terms):
    """Core guardrail: no raw entity value must appear in approved_terms.jsonl."""
    job = Job(filename="test_doc.pdf", id="deadbeef" * 4)

    entities = [
        _make_entity("PATIENT_NAME",     FAKE_NAME),
        _make_entity("ADDRESS",          FAKE_ADDRESS),
        _make_entity("PHONE",            FAKE_PHONE),
        _make_entity("DATE",             FAKE_DATE),
        _make_entity("BANK_INFORMATION", FAKE_IBAN),
    ]

    _log_approved_terms(job, entities)

    raw_content = tmp_approved_terms.read_text()

    for phi in RAW_PHI:
        assert phi not in raw_content, (
            f"RAW PHI LEAK: '{phi}' found in approved_terms.jsonl\n"
            f"Content:\n{raw_content}"
        )


def test_learning_store_contains_expected_safe_fields(tmp_approved_terms):
    """Learning entries must have structural features but no reversible values."""
    job = Job(filename="doc.pdf", id="cafebabe" * 4)
    entities = [_make_entity("PATIENT_NAME", FAKE_NAME)]

    _log_approved_terms(job, entities)

    entries = _read_entries(tmp_approved_terms)
    assert len(entries) == 1
    entry = entries[0]

    # Required safe fields
    assert "label" in entry
    assert "text_shape" in entry
    assert "char_len_bucket" in entry
    assert "job_id_hash" in entry
    assert "filename_hash" in entry
    assert "accepted" in entry

    # Forbidden fields
    assert "text" not in entry
    assert "filename" not in entry
    assert "job_id" not in entry
    assert "replacement" not in entry


def test_dismissed_entities_not_logged(tmp_approved_terms):
    """Dismissed entities must not appear in the learning store at all."""
    job = Job(filename="doc.pdf", id="0" * 32)
    entities = [
        Entity(label="DATE", text=FAKE_DATE, start=0, end=len(FAKE_DATE),
               replacement=FAKE_DATE, approved=False, dismissed=True),
    ]

    _log_approved_terms(job, entities)

    assert tmp_approved_terms.read_text().strip() == ""


def test_text_shape_removes_all_original_chars():
    """_text_shape must not preserve any letter or digit from the input."""
    import re
    for phi in RAW_PHI:
        shape = _text_shape(phi)
        # No original letters or digits should survive
        original_letters = set(re.findall(r'[A-Za-z]', phi))
        shape_non_meta   = re.sub(r'[A+a+0+\s\-\.,/()]', '', shape)
        assert not any(ch in shape_non_meta for ch in original_letters), (
            f"Original chars survived in shape: '{phi}' → '{shape}'"
        )


def test_len_bucket_covers_all_sizes():
    assert _len_bucket(1)  == "1-4"
    assert _len_bucket(4)  == "1-4"
    assert _len_bucket(5)  == "5-7"
    assert _len_bucket(7)  == "5-7"
    assert _len_bucket(8)  == "8-12"
    assert _len_bucket(12) == "8-12"
    assert _len_bucket(13) == "13-20"
    assert _len_bucket(20) == "13-20"
    assert _len_bucket(21) == "21+"
    assert _len_bucket(999) == "21+"
