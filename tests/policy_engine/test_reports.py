"""P2 tests: enriched reports, preview.md, Zone 1 security, max_allowable decision."""

import json
import zipfile
from pathlib import Path

from app.storage.jobs import Entity
from app.policy_engine.engine import prepare
from app.policy_engine.models import PolicyRequest
from app.policy_engine.package import (
    _assert_no_token_leak,
    _build_risk_report,
    _build_transformation_log,
    _build_usefulness_report,
    build_preview_md,
    save_package,
    zip_package,
    _packages_dir,
)
from app.policy_engine.profiles import load_profile

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_DOC = (FIXTURES / "sample_document.md").read_text()


def _req(task="translation", strictness="balanced", job_id="RPT_TEST"):
    return PolicyRequest(
        job_id=job_id,
        task=task,
        strictness_mode=strictness,
        consumer_type="trusted_vendor",
        provider_risk="trusted_vendor",
        document_text=SAMPLE_DOC,
        entities=[
            Entity(id="e1", label="PATIENT_NAME", text="Maria Schmidt",
                   start=51, end=64, replacement="[PATIENT_NAME]",
                   approved=True, dismissed=False),
            Entity(id="e2", label="DOB", text="08.07.1972",
                   start=81, end=91, replacement="[DOB]",
                   approved=True, dismissed=False),
        ],
        document_date="11.10.2023",
    )


# ── Risk report ───────────────────────────────────────────────────────────────

def test_risk_report_has_summary():
    pkg = prepare(_req(job_id="RISK1"), save=False)
    report = _build_risk_report(pkg)
    assert "summary" in report
    assert isinstance(report["summary"], str)
    assert len(report["summary"]) > 10


def test_risk_report_separates_direct_and_quasi():
    pkg = prepare(_req(job_id="RISK2"), save=False)
    report = _build_risk_report(pkg)
    checks = report["checks"]
    categories = {v["category"] for v in checks.values()}
    assert "direct_identifier" in categories
    assert "quasi_identifier" in categories


def test_risk_report_has_descriptions():
    pkg = prepare(_req(job_id="RISK3"), save=False)
    report = _build_risk_report(pkg)
    for check_name, check_data in report["checks"].items():
        assert "description" in check_data
        assert len(check_data["description"]) > 5


# ── Usefulness report ─────────────────────────────────────────────────────────

def test_usefulness_report_has_weighted_checks():
    pkg = prepare(_req(job_id="USE1"), save=False)
    profile = load_profile("translation")
    report = _build_usefulness_report(pkg, _req(), profile)
    assert "weighted_checks" in report
    for name, data in report["weighted_checks"].items():
        assert "pass" in data
        assert "weight" in data
        assert "contribution" in data


def test_usefulness_report_has_summary():
    pkg = prepare(_req(job_id="USE2"), save=False)
    profile = load_profile("translation")
    report = _build_usefulness_report(pkg, _req(), profile)
    assert "summary" in report
    assert "threshold" in report["summary"]


def test_usefulness_report_includes_task_and_profile():
    pkg = prepare(_req(job_id="USE3"), save=False)
    profile = load_profile("translation")
    report = _build_usefulness_report(pkg, _req(), profile)
    assert report["task"] == "translation"
    assert report["profile"] == "translation"
    assert report["strictness"] == "balanced"


def test_usefulness_report_contributions_sum_to_score():
    pkg = prepare(_req(job_id="USE4"), save=False)
    profile = load_profile("translation")
    report = _build_usefulness_report(pkg, _req(), profile)
    total_contribution = sum(
        v["contribution"] for v in report["weighted_checks"].values()
    )
    assert abs(total_contribution - report["score"]) < 0.01


# ── Transformation log ────────────────────────────────────────────────────────

def test_transformation_log_has_no_original_text():
    pkg = prepare(_req(job_id="LOG1"), save=False)
    log = _build_transformation_log(pkg)
    raw = json.dumps(log)
    assert "Maria Schmidt" not in raw
    assert "08.07.1972" not in raw
    assert "original_text" not in raw


def test_transformation_log_has_descriptions():
    pkg = prepare(_req(job_id="LOG2"), save=False)
    log = _build_transformation_log(pkg)
    for entry in log["entries"]:
        assert "description" in entry
        assert len(entry["description"]) > 5


def test_transformation_log_has_by_action_summary():
    pkg = prepare(_req(job_id="LOG3"), save=False)
    log = _build_transformation_log(pkg)
    assert "by_action" in log
    assert "total_entities_processed" in log
    assert log["total_entities_processed"] == len(pkg.transformation_log)


def test_transformation_log_each_entry_has_output():
    pkg = prepare(_req(job_id="LOG4"), save=False)
    log = _build_transformation_log(pkg)
    for entry in log["entries"]:
        assert "output" in entry
        assert "action_applied" in entry
        assert "label" in entry


# ── Preview markdown ──────────────────────────────────────────────────────────

def test_preview_md_generated():
    pkg = prepare(_req(job_id="PRV1"), save=False)
    profile = load_profile("translation")
    md = build_preview_md(pkg, _req(), profile)
    assert "# Confidoc Prepared Package" in md
    assert "Privacy Actions Applied" in md
    assert "Usefulness Assessment" in md
    assert "Risk Assessment" in md


def test_preview_md_shows_recommendation():
    pkg = prepare(_req(job_id="PRV2"), save=False)
    profile = load_profile("translation")
    md = build_preview_md(pkg, _req(), profile)
    assert any(word in md for word in ("approved", "review_required", "reject"))


def test_preview_md_no_phi():
    pkg = prepare(_req(job_id="PRV3"), save=False)
    profile = load_profile("translation")
    md = build_preview_md(pkg, _req(), profile)
    assert "Maria Schmidt" not in md
    assert "08.07.1972" not in md


def test_preview_md_zone1_note():
    pkg = prepare(_req(job_id="PRV4"), save=False)
    profile = load_profile("translation")
    md = build_preview_md(pkg, _req(), profile)
    assert "Zone 1" in md
    assert "not" in md.lower() or "never" in md.lower()


# ── Manifest ──────────────────────────────────────────────────────────────────

def test_manifest_has_actions_summary():
    pkg = prepare(_req(job_id="MAN1"), save=True)
    pkg_dir = _packages_dir() / pkg.package_id
    manifest = json.loads((pkg_dir / "manifest.json").read_text())
    assert "actions_summary" in manifest
    assert isinstance(manifest["actions_summary"], dict)


def test_manifest_has_recommendation():
    pkg = prepare(_req(job_id="MAN2"), save=True)
    pkg_dir = _packages_dir() / pkg.package_id
    manifest = json.loads((pkg_dir / "manifest.json").read_text())
    assert manifest["recommended_action"] in ("approved", "review_required", "reject")


def test_manifest_checksums_match_files():
    pkg = prepare(_req(job_id="MAN3"), save=True)
    pkg_dir = _packages_dir() / pkg.package_id
    import hashlib
    manifest = json.loads((pkg_dir / "manifest.json").read_text())
    files_block = manifest.get("files", {})
    for fname, info in files_block.items():
        fpath = pkg_dir / fname
        if fpath.exists():
            actual = hashlib.sha256(fpath.read_bytes()).hexdigest()
            assert actual == info["sha256"], f"Checksum mismatch for {fname}"


# ── Max allowable decision ────────────────────────────────────────────────────

def test_max_allowable_decision_recorded():
    req = PolicyRequest(
        job_id="MAX_RPT_1",
        task="translation",
        strictness_mode="max_allowable",
        consumer_type="trusted_vendor",
        provider_risk="trusted_vendor",
        document_text=SAMPLE_DOC,
        entities=[
            Entity(id="e1", label="PATIENT_NAME", text="Maria Schmidt",
                   start=51, end=64, replacement="[PATIENT_NAME]",
                   approved=True, dismissed=False),
        ],
        document_date="11.10.2023",
    )
    pkg = prepare(req, save=False)
    assert pkg.max_allowable_decision is not None
    assert pkg.max_allowable_decision.selected != ""
    assert len(pkg.max_allowable_decision.levels_tried) >= 1
    assert pkg.max_allowable_decision.reason


def test_max_allowable_decision_in_manifest():
    req = PolicyRequest(
        job_id="MAX_RPT_2",
        task="translation",
        strictness_mode="max_allowable",
        consumer_type="trusted_vendor",
        provider_risk="trusted_vendor",
        document_text=SAMPLE_DOC,
        entities=[],
        document_date="11.10.2023",
    )
    pkg = prepare(req, save=True)
    pkg_dir = _packages_dir() / pkg.package_id
    manifest = json.loads((pkg_dir / "manifest.json").read_text())
    assert "max_allowable_decision" in manifest
    assert "selected" in manifest["max_allowable_decision"]
    assert "reason" in manifest["max_allowable_decision"]


def test_max_allowable_decision_in_preview():
    req = PolicyRequest(
        job_id="MAX_RPT_3",
        task="translation",
        strictness_mode="max_allowable",
        consumer_type="trusted_vendor",
        provider_risk="trusted_vendor",
        document_text=SAMPLE_DOC,
        entities=[],
        document_date="11.10.2023",
    )
    pkg = prepare(req, save=False)
    profile = load_profile("translation")
    md = build_preview_md(pkg, req, profile)
    assert "max_allowable" in md.lower() or "Strictness Selection" in md


# ── Zone 1 security ───────────────────────────────────────────────────────────

def test_no_phi_in_package_files():
    pkg = prepare(_req(job_id="SEC1"), save=True)
    pkg_dir = _packages_dir() / pkg.package_id
    for f in pkg_dir.iterdir():
        if f.suffix in (".json", ".md"):
            content = f.read_text()
            assert "Maria Schmidt" not in content, f"PHI in {f.name}"
            assert "policy_token_map" not in content, f"token_map key in {f.name}"


def test_zip_does_not_contain_mapping():
    pkg = prepare(_req(job_id="SEC2"), save=True)
    pkg_dir = _packages_dir() / pkg.package_id
    zip_path = zip_package(pkg_dir)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert not any("mapping" in n.lower() or ".enc" in n for n in names)
    assert "prepared.md" in names
    assert "preview.md" in names
