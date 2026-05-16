"""Tests: prepared package contents, checksums, no PHI leakage."""

import json
import zipfile
from pathlib import Path

from app.storage.jobs import Entity
from app.policy_engine.engine import prepare
from app.policy_engine.models import PolicyRequest
from app.policy_engine.package import save_package, zip_package

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_DOC = (FIXTURES / "sample_document.md").read_text()


def _request(task="translation"):
    return PolicyRequest(
        job_id="PKG_TEST_001",
        task=task,
        strictness_mode="balanced",
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


def test_package_includes_prepared_md(tmp_path):
    req = _request()
    pkg = prepare(req, save=False)
    assert pkg.prepared_text
    assert len(pkg.prepared_text) > 10


def test_package_does_not_include_mapping(tmp_path):
    req = _request()
    pkg = prepare(req, save=True)
    from app.policy_engine.package import _packages_dir
    pkg_dir = _packages_dir() / pkg.package_id
    files = [f.name for f in pkg_dir.iterdir()]
    assert "mapping" not in " ".join(files).lower()
    assert not any(".enc" in f for f in files)


def test_package_includes_manifest():
    req = _request()
    pkg = prepare(req, save=True)
    from app.policy_engine.package import _packages_dir
    pkg_dir = _packages_dir() / pkg.package_id
    manifest_path = pkg_dir / "manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["job_id"] == "PKG_TEST_001"
    assert "risk_score" in manifest
    assert "prepared_at" in manifest


def test_package_includes_risk_report():
    req = _request()
    pkg = prepare(req, save=True)
    from app.policy_engine.package import _packages_dir
    pkg_dir = _packages_dir() / pkg.package_id
    assert (pkg_dir / "risk_report.json").exists()


def test_package_includes_usefulness_report():
    req = _request()
    pkg = prepare(req, save=True)
    from app.policy_engine.package import _packages_dir
    pkg_dir = _packages_dir() / pkg.package_id
    assert (pkg_dir / "usefulness_report.json").exists()


def test_package_includes_transformation_log():
    req = _request()
    pkg = prepare(req, save=True)
    from app.policy_engine.package import _packages_dir
    pkg_dir = _packages_dir() / pkg.package_id
    log_path = pkg_dir / "transformation_log.json"
    assert log_path.exists()
    log = json.loads(log_path.read_text())
    assert "entries" in log
    assert isinstance(log["entries"], list)


def test_manifest_has_checksums():
    req = _request()
    pkg = prepare(req, save=True)
    from app.policy_engine.package import _packages_dir
    pkg_dir = _packages_dir() / pkg.package_id
    manifest = json.loads((pkg_dir / "manifest.json").read_text())
    assert "files" in manifest
    assert "prepared.md" in manifest["files"]
    assert "sha256" in manifest["files"]["prepared.md"]


def test_zip_contains_only_safe_files():
    req = _request()
    pkg = prepare(req, save=True)
    from app.policy_engine.package import _packages_dir
    pkg_dir = _packages_dir() / pkg.package_id
    zip_path = zip_package(pkg_dir)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert "prepared.md" in names
    assert "manifest.json" in names
    assert not any(".enc" in n or "mapping" in n.lower() for n in names)


def test_prepared_text_does_not_contain_original_name():
    req = _request()
    pkg = prepare(req, save=False)
    assert "Maria Schmidt" not in pkg.prepared_text


def test_max_allowable_selects_strictest_passing():
    req = PolicyRequest(
        job_id="MAX_TEST",
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
    assert pkg.selected_strictness in ("minimal", "balanced", "strict", "maximum")
    assert pkg.usefulness.passes or pkg.selected_strictness == "minimal"
