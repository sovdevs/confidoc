"""Build and persist the prepared document package for Zone 2.

ZONE 1 SECURITY CONTRACT
────────────────────────
The following MUST NEVER appear in any package file, ZIP, audit log, or LLM prompt:
  • policy_token_map   (token → original PHI mapping)
  • original_text      (raw PHI from TransformationEntry)
  • source document    (original extracted markdown)
  • original PDF

The package contains only:
  prepared.md               → anonymized document
  manifest.json             → summary, counts, decision, checksums
  risk_report.json          → re-identification risk assessment
  usefulness_report.json    → task usefulness assessment
  transformation_log.json   → what was done (no original PHI)
  preview.md                → human-readable summary for UI / review

_assert_no_token_leak() verifies this after every write.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import zipfile
from collections import Counter, defaultdict
from datetime import timezone
from pathlib import Path
from typing import Any

from app.policy_engine.models import PreparedPackage, PolicyRequest
from app.policy_engine.profiles import ProfileConfig

# ── Action descriptions (human-readable) ─────────────────────────────────────
_ACTION_DESCRIPTIONS: dict[str, str] = {
    "stable_token":         "Replaced with numbered stable placeholder",
    "remove":               "Removed from prepared document",
    "age_from_dob":         "Date of birth converted to exact age",
    "age_band_from_dob":    "Date of birth converted to age band (10-year range)",
    "relative_date":        "Date replaced with relative chronology marker",
    "coarse_relative_date": "Date replaced with approximate relative week offset",
    "generalize":           "Location generalized to region/country",
    "date_shift":           "Date shifted by consistent offset within this job",
    "keep":                 "Preserved in prepared document (clinically required)",
    "flag_for_review":      "Preserved with reidentification risk flag",
}

# ── Check descriptions ────────────────────────────────────────────────────────
_CHECK_DESCRIPTIONS: dict[str, str] = {
    "diagnoses_preserved":                 "Medical diagnoses remain in document",
    "medications_preserved":               "Medication names remain in document",
    "measurements_preserved":              "Clinical measurements (values + units) remain",
    "procedures_preserved":                "Medical procedures remain in document",
    "chronology_preserved":                "Date or time markers remain for chronology",
    "placeholders_stable":                 "All tokens follow stable numbered format",
    "section_structure_preserved":         "Document headings/sections largely intact",
    "enough_text_remaining":               "Sufficient text remains for the task",
    "terminology_preserved":               "Domain-specific terminology still present",
    "sentence_meaning_preserved":          "Most non-identifier words remain",
    "gender_context_preserved":            "Gender-relevant language preserved for translation",
    "coded_diagnoses_preserved":           "ICD codes or diagnosis terms present",
    "demographic_band_preserved":          "Age band or demographic range present",
    "enough_structured_content_remaining": "Enough structured content for feature extraction",
    "treatment_plan_preserved":            "Treatment plan / follow-up context remains",
    "general_topic_preserved":             "General subject of document still discernible",
    "no_direct_identifiers":               "No obvious direct identifier patterns found",
    "enough_context_for_demo":             "Sufficient context for demonstration use",
}

_RISK_CHECK_DESCRIPTIONS: dict[str, str] = {
    "no_full_names":        "No full name patterns (First Last) detected",
    "no_addresses":         "No street address patterns detected",
    "no_emails":            "No email addresses detected",
    "no_phone_numbers":     "No phone number patterns detected",
    "no_insurance_ids":     "No insurance/ID number patterns detected",
    "no_exact_dob":         "No exact date of birth detected",
    "no_postal_codes":      "No German postal codes detected",
    "rare_condition_clear": "No rare/identifying condition terms detected",
    "few_precise_dates":    "Three or fewer precise calendar dates remain",
    "no_flagged_entities":  "No entities flagged for reidentification risk",
}


# ── SHA256 ────────────────────────────────────────────────────────────────────

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _packages_dir() -> Path:
    from app.config import settings
    d = settings.prepared_packages_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Report builders ───────────────────────────────────────────────────────────

def _build_risk_report(package: PreparedPackage) -> dict:
    risk = package.risk
    if not risk:
        return {"error": "No risk result available"}

    direct_checks = ["no_full_names", "no_addresses", "no_emails",
                     "no_phone_numbers", "no_insurance_ids", "no_exact_dob"]
    quasi_checks  = ["rare_condition_clear", "few_precise_dates",
                     "no_postal_codes", "no_flagged_entities"]

    enriched_checks: dict[str, Any] = {}
    for name, passed in risk.checks.items():
        enriched_checks[name] = {
            "pass": passed,
            "category": "direct_identifier" if name in direct_checks else "quasi_identifier",
            "description": _RISK_CHECK_DESCRIPTIONS.get(name, name),
        }

    direct_pass = sum(1 for c in direct_checks if risk.checks.get(c, True))
    quasi_pass  = sum(1 for c in quasi_checks  if risk.checks.get(c, True))

    return {
        "risk_score":               risk.risk_score,
        "direct_identifier_risk":   risk.direct_identifier_risk,
        "quasi_identifier_risk":    risk.quasi_identifier_risk,
        "checks":                   enriched_checks,
        "warnings":                 risk.warnings,
        "summary": (
            f"{direct_pass}/{len(direct_checks)} direct identifier checks passed, "
            f"{quasi_pass}/{len(quasi_checks)} quasi-identifier checks passed. "
            f"Overall risk: {risk.risk_score}."
        ),
    }


def _build_usefulness_report(package: PreparedPackage, request: PolicyRequest, profile: ProfileConfig) -> dict:
    use = package.usefulness
    if not use:
        return {"error": "No usefulness result available"}

    weighted_checks: dict[str, Any] = {}
    for name, passed in use.checks.items():
        weight = profile.usefulness.weights.get(name, 0.0)
        weighted_checks[name] = {
            "pass":         passed,
            "weight":       weight,
            "contribution": round(weight, 3) if passed else 0.0,
            "description":  _CHECK_DESCRIPTIONS.get(name, name),
        }

    passing = sum(1 for v in weighted_checks.values() if v["pass"])
    total   = len(weighted_checks)
    status  = "PASS ✓" if use.passes else "FAIL ✗"

    return {
        "task":             request.task,
        "profile":          profile.name,
        "strictness":       package.selected_strictness,
        "score":            use.score,
        "threshold":        use.threshold,
        "passes":           use.passes,
        "status":           status,
        "weighted_checks":  weighted_checks,
        "notes":            use.notes,
        "summary": (
            f"{passing}/{total} checks passed. "
            f"Score {use.score:.2f} {'≥' if use.passes else '<'} threshold {use.threshold:.2f}. "
            f"Status: {status}."
        ),
    }


def _build_transformation_log(package: PreparedPackage) -> dict:
    """Zone 2 safe: no original_text, no PHI."""
    by_action: Counter = Counter(e.action_applied for e in package.transformation_log)
    flagged = [e for e in package.transformation_log if e.warning]

    entries = []
    for e in package.transformation_log:
        entries.append({
            "entity_id":      e.entity_id,
            "label":          e.label,
            "action_applied": e.action_applied,
            "output":         e.output,
            "strictness":     e.strictness,
            "description":    _ACTION_DESCRIPTIONS.get(e.action_applied, e.action_applied),
            "warning":        e.warning,
            # original_text intentionally omitted — Zone 1 only
        })

    return {
        "total_entities_processed": len(package.transformation_log),
        "by_action": dict(by_action),
        "flagged_for_review": len(flagged),
        "entries": entries,
    }


def _build_max_allowable_section(package: PreparedPackage) -> dict | None:
    d = package.max_allowable_decision
    if not d:
        return None
    return {
        "mode":           "max_allowable",
        "provider_floor": d.provider_floor,
        "levels_tried":   d.levels_tried,
        "scores_by_level":{k: round(v, 3) for k, v in d.scores_by_level.items()},
        "selected":       d.selected,
        "reason":         d.reason,
    }


def _build_manifest(
    package: PreparedPackage,
    request: PolicyRequest,
    profile: ProfileConfig,
    file_checksums: dict[str, str],
) -> dict:
    by_action: Counter = Counter(e.action_applied for e in package.transformation_log)
    direct_removed = by_action.get("remove", 0)
    tokenized      = by_action.get("stable_token", 0)
    generalized    = sum(by_action.get(a, 0) for a in
                         ("generalize", "age_from_dob", "age_band_from_dob",
                          "relative_date", "coarse_relative_date"))

    risk_score = package.risk.risk_score if package.risk else "unknown"
    use_score  = package.usefulness.score if package.usefulness else 0.0
    use_passes = package.usefulness.passes if package.usefulness else False
    rec = _recommended_action(package)

    manifest: dict[str, Any] = {
        "package_id":                   package.package_id,
        "job_id":                       package.job_id,
        "task":                         request.task,
        "profile":                      profile.name,
        "strictness":                   package.selected_strictness,
        "consumer_type":                request.consumer_type,
        "provider_risk":                request.provider_risk,
        "prepared_at":                  package.prepared_at.isoformat(),
        "direct_identifiers_removed":   direct_removed,
        "entities_tokenized":           tokenized,
        "entities_generalized":         generalized,
        "total_entities_processed":     len(package.transformation_log),
        "clinical_facts_preserved_score": use_score,
        "usefulness_passes":            use_passes,
        "risk_score":                   risk_score,
        "recommended_action":           rec,
        "actions_summary":              dict(by_action),
        "warnings":                     package.warnings,
        "files":                        {k: {"sha256": v} for k, v in file_checksums.items()},
        # NEVER include: policy_token_map, original document text, raw PHI
    }

    decision = _build_max_allowable_section(package)
    if decision:
        manifest["max_allowable_decision"] = decision

    return manifest


def _recommended_action(package: PreparedPackage) -> str:
    risk = package.risk.risk_score if package.risk else "unknown"
    use_passes = package.usefulness.passes if package.usefulness else False
    if risk == "high":
        return "reject"
    if risk == "medium" or not use_passes:
        return "review_required"
    return "approved"


# ── Preview markdown ──────────────────────────────────────────────────────────

def build_preview_md(
    package: PreparedPackage,
    request: PolicyRequest,
    profile: ProfileConfig,
) -> str:
    by_action: Counter = Counter(e.action_applied for e in package.transformation_log)
    flagged = [e for e in package.transformation_log if e.warning]
    rec = _recommended_action(package)
    rec_icon = {"approved": "✓", "review_required": "⚠", "reject": "✗"}.get(rec, "?")

    use = package.usefulness
    risk = package.risk

    lines = [
        "# Confidoc Prepared Package",
        "",
        f"| Field | Value |",
        f"|---|---|",
        f"| Package ID | `{package.package_id}` |",
        f"| Job ID | `{package.job_id}` |",
        f"| Task | {request.task} |",
        f"| Profile | {profile.name} |",
        f"| Strictness applied | **{package.selected_strictness}** |",
        f"| Prepared at | {package.prepared_at.strftime('%Y-%m-%d %H:%M UTC')} |",
        f"| Recommendation | {rec_icon} **{rec}** |",
        "",
        "---",
        "",
        "## Privacy Actions Applied",
        "",
        "| Action | Count | Meaning |",
        "|--------|-------|---------|",
    ]

    for action, count in sorted(by_action.items(), key=lambda x: -x[1]):
        desc = _ACTION_DESCRIPTIONS.get(action, action)
        lines.append(f"| `{action}` | {count} | {desc} |")

    lines += ["", "---", ""]

    # Usefulness
    if use:
        status = "✓ PASS" if use.passes else "✗ FAIL"
        lines += [
            f"## Usefulness Assessment: {status} ({use.score:.2f} / {use.threshold:.2f} threshold)",
            "",
            "| Check | Weight | Result |",
            "|-------|--------|--------|",
        ]
        for name, passed in use.checks.items():
            weight = profile.usefulness.weights.get(name)
            w_str = f"{weight:.0%}" if weight else "—"
            icon = "✓" if passed else "✗"
            lines.append(f"| {_CHECK_DESCRIPTIONS.get(name, name)} | {w_str} | {icon} |")

    lines += ["", "---", ""]

    # Risk
    if risk:
        risk_icon = {"low": "✓", "medium": "⚠", "high": "✗"}.get(risk.risk_score, "?")
        lines += [
            f"## Risk Assessment: {risk_icon} {risk.risk_score.upper()}",
            "",
            "| Category | Level |",
            "|----------|-------|",
            f"| Direct identifiers | {risk.direct_identifier_risk} |",
            f"| Quasi-identifiers | {risk.quasi_identifier_risk} |",
        ]
        if risk.warnings:
            lines += ["", "**Risk warnings:**", ""]
            for w in risk.warnings:
                lines.append(f"- {w}")

    lines += ["", "---", ""]

    # Max allowable decision
    d = package.max_allowable_decision
    if d:
        lines += [
            "## Strictness Selection (max_allowable mode)",
            "",
            f"**Provider floor:** {d.provider_floor}",
            "",
            "| Level tried | Usefulness score | Selected? |",
            "|-------------|-----------------|-----------|",
        ]
        for lvl in d.levels_tried:
            score = d.scores_by_level.get(lvl, 0.0)
            selected = "✓ **selected**" if lvl == d.selected else ""
            lines.append(f"| {lvl} | {score:.2f} | {selected} |")
        lines += ["", f"**Reason:** {d.reason}", ""]

    lines += ["---", ""]

    # Flagged entities
    if flagged:
        lines += [
            "## Flagged Entities (Reidentification Risk)",
            "",
            "| Label | Flag |",
            "|-------|------|",
        ]
        for e in flagged:
            lines.append(f"| `{e.label}` | {e.warning} |")
        lines += [""]

    lines += [
        "---",
        "",
        "## Package Contents",
        "",
        "| File | Purpose |",
        "|------|---------|",
        "| `prepared.md` | Anonymized/pseudonymized document for Zone 2 |",
        "| `manifest.json` | Package summary, counts, decision, checksums |",
        "| `risk_report.json` | Re-identification risk assessment |",
        "| `usefulness_report.json` | Task usefulness assessment |",
        "| `transformation_log.json` | Entity transformation audit trail (no PHI) |",
        "| `preview.md` | This human-readable summary |",
        "",
        "> **Zone 1 note:** The token-to-original mapping is stored encrypted in Zone 1 only.",
        "> It is **not** included in this package.",
    ]

    return "\n".join(lines)


# ── Security assertion ────────────────────────────────────────────────────────

_FORBIDDEN_IN_PACKAGE = ["policy_token_map", "original_text"]


def _assert_no_token_leak(pkg_dir: Path, package: PreparedPackage) -> None:
    """Assert that no Zone 1 secrets appear in any package file."""
    for f in pkg_dir.iterdir():
        if f.suffix not in (".json", ".md"):
            continue
        content = f.read_text(encoding="utf-8")
        for forbidden in _FORBIDDEN_IN_PACKAGE:
            if forbidden in content:
                raise RuntimeError(
                    f"Zone 1 secret '{forbidden}' found in package file {f.name}. "
                    "This is a critical security violation."
                )
        # Verify no actual PHI token values leaked (token_map values are PHI)
        for original_text in package.policy_token_map.values():
            if len(original_text) > 4 and original_text in content:
                raise RuntimeError(
                    f"PHI value appears in package file {f.name}. "
                    "Check transformation_log for original_text leakage."
                )


# ── Main save function ────────────────────────────────────────────────────────

def save_package(
    package: PreparedPackage,
    request: PolicyRequest,
    profile: ProfileConfig,
) -> Path:
    """Write the enriched prepared package to disk. Returns the package directory."""
    pkg_dir = _packages_dir() / package.package_id
    pkg_dir.mkdir(parents=True, exist_ok=True)

    # Write prepared markdown
    prepared_md = pkg_dir / "prepared.md"
    prepared_md.write_text(package.prepared_text, encoding="utf-8")

    # Build reports
    risk_dict        = _build_risk_report(package)
    usefulness_dict  = _build_usefulness_report(package, request, profile)
    log_dict         = _build_transformation_log(package)
    preview_md_text  = build_preview_md(package, request, profile)

    # Write reports
    (pkg_dir / "risk_report.json").write_text(
        json.dumps(risk_dict, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (pkg_dir / "usefulness_report.json").write_text(
        json.dumps(usefulness_dict, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (pkg_dir / "transformation_log.json").write_text(
        json.dumps(log_dict, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (pkg_dir / "preview.md").write_text(preview_md_text, encoding="utf-8")

    # Build manifest with checksums — computed from actual written file bytes
    def _file_sha256(name: str) -> str:
        import hashlib
        return hashlib.sha256((pkg_dir / name).read_bytes()).hexdigest()

    checksums = {
        "prepared.md":             _file_sha256("prepared.md"),
        "risk_report.json":        _file_sha256("risk_report.json"),
        "usefulness_report.json":  _file_sha256("usefulness_report.json"),
        "transformation_log.json": _file_sha256("transformation_log.json"),
        "preview.md":              _file_sha256("preview.md"),
    }
    manifest = _build_manifest(package, request, profile, checksums)
    package.manifest = manifest
    (pkg_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str, ensure_ascii=False), encoding="utf-8"
    )

    # Security assertion — must run last
    _assert_no_token_leak(pkg_dir, package)

    return pkg_dir


_ZIP_SAFE_EXTS = {".md", ".json", ".tmx", ".xliff", ".sdlxliff", ".docx", ".csv"}


def zip_package(pkg_dir: Path) -> Path:
    """Create a Zone 2 transfer ZIP. Never includes mapping files or original PHI."""
    zip_path = pkg_dir.parent / f"{pkg_dir.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(pkg_dir.iterdir()):
            if f.suffix in _ZIP_SAFE_EXTS:
                zf.write(f, f.name)
    return zip_path
