# Confidoc Policy Engine

## Purpose

The Policy Engine is the middle layer between identifiable source documents (Zone 1) and any downstream reviewer, translator, LLM, or researcher (Zone 2).

Its job is to answer:

> What is the minimum necessary information this user or model needs to perform this task?

and produce a **prepared document package** accordingly.

---

## Zone Architecture

```
Zone 1: Identifiable Source Zone
  original PDF · extracted Markdown · approved entities
  encrypted token mapping · source backup

        ↓  PolicyRequest

  Confidoc Policy Engine
  task · profile · strictness · consumer type
  transformation rules · usefulness checks · risk checks

        ↓  PreparedPackage (no PHI, no mapping)

Zone 2: Anonymized Processing Zone
  human reviewer · translator · LLM · researcher · vendor

        ↓  (authorized rehydration only)

Zone 3: Controlled Rehydration Zone
  authorized clinician/HCP · decrypt mapping · restore identifiers
```

**Critical rule:** Zone 2 never receives the original PDF, original text, or decrypted token mapping.

---

## Usage

```python
from app.policy_engine.engine import prepare
from app.policy_engine.models import PolicyRequest

pkg = prepare(PolicyRequest(
    job_id="JOB_123",
    task="clinical_summary",           # drives profile selection
    strictness_mode="max_allowable",   # or: minimal|balanced|strict|maximum
    consumer_type="cloud_llm",
    provider_risk="cloud_llm",
    document_text=extracted_markdown,  # Zone 1 — original text
    entities=approved_entities,        # list[Entity] from HITL review
    document_date="11.10.2023",        # used for DOB→age calculations
))

# Zone 2 outputs:
pkg.prepared_text          # anonymized markdown
pkg.manifest               # package summary

# Zone 1 only — NEVER send to Zone 2:
pkg.policy_token_map       # {token: original_text} — encrypted, Zone 1 only
```

---

## Tasks and Profiles

| Task | Profile | Default strictness | Usefulness threshold |
|------|---------|-------------------|---------------------|
| `translation` | translation | balanced | 0.78 |
| `clinical_summary` | clinical_summary | strict | 0.90 |
| `ml_feature_extraction` | ml_feature_extraction | strict | 0.70 |
| `research_extract` | research_extract | maximum | 0.60 |
| `public_release` | public_release | maximum | 0.40 |

Profiles are YAML files in `profiles/`. Each profile defines entity rules, usefulness weights, and optional per-level strictness overrides.

---

## Transformation Actions

| Action | Output | Example |
|--------|--------|---------|
| `stable_token` | Numbered placeholder | `[PATIENT_001]` |
| `remove` | Label placeholder | `[REMOVED_ADDRESS]` |
| `age_from_dob` | Exact age | `age 51` |
| `age_band_from_dob` | 10-year band | `age band 50-59` |
| `relative_date` (token) | Numbered token | `[DATE_001]` |
| `relative_date` (offset) | Day offset | `Day -30` |
| `coarse_relative_date` | Week offset | `[~-4w from report]` |
| `generalize` | Region/country | `Bayern`, `[REGION]` |
| `date_shift` | Shifted date | consistent shift per job |
| `keep` | Original text | (clinical content) |
| `flag_for_review` | Original text + risk flag | (quasi-identifier) |

**DOB reference date priority:** REPORT_DATE entity → document_date → today's date.

---

## Strictness Levels

| Level | Behaviour |
|-------|-----------|
| `minimal` | Direct identifiers tokenized; most content preserved |
| `balanced` | Profile default rules applied |
| `strict` | `keep` → `flag_for_review` for quasi-identifiers |
| `maximum` | `stable_token` → `remove` (unless `rehydration_required: true`); `flag_for_review` → `remove` |
| `max_allowable` | Tries `maximum → strict → balanced → minimal`; selects strictest passing profile's usefulness threshold |

**Provider risk floor** — strictness is never lower than the consumer's risk level requires:

| Consumer | Minimum strictness |
|----------|-------------------|
| `trusted_internal` | minimal |
| `trusted_vendor` | balanced |
| `local_llm` | balanced |
| `cloud_llm` | strict |
| `external_researcher` | strict |
| `public` | maximum |

---

## Prepared Package

Every call to `prepare()` writes a package to `data/prepared_packages/<package_id>/`:

```
prepared.md               ← anonymized document for Zone 2
manifest.json             ← summary, counts, decision, recommendation, checksums
risk_report.json          ← direct/quasi-identifier risk assessment
usefulness_report.json    ← weighted check breakdown by profile
transformation_log.json   ← what was done to each entity (no original PHI)
preview.md                ← human-readable summary for UI review
```

**What is never included:**
- `policy_token_map` (token → original PHI)
- original document text
- original PDF
- raw PHI of any kind

`_assert_no_token_leak()` runs after every write and raises `RuntimeError` if any Zone 1 secret appears in a package file.

To create a transfer ZIP:
```python
from app.policy_engine.package import zip_package, _packages_dir
zip_path = zip_package(_packages_dir() / pkg.package_id)
```

---

## Profile Format

```yaml
profile: translation
description: Prepare for human/LLM translation.
default_strictness: balanced
rehydration_required: true   # preserves stable_token even at maximum

usefulness:
  threshold: 0.78
  weights:
    sentence_meaning_preserved: 0.30
    terminology_preserved: 0.25
    placeholders_stable: 0.20
    gender_context_preserved: 0.10
    section_structure_preserved: 0.10
    chronology_preserved: 0.05

entity_rules:
  PATIENT_NAME:
    action: stable_token
    token_type: PATIENT          # → [PATIENT_001]
  ADDRESS:
    action: remove
  DOB:
    action: age_from_dob
  LOCATION:
    action: generalize
    location_mode: exact_to_placeholder   # city_to_region | city_to_country | remove
  DIAGNOSIS:
    action: keep

strictness_overrides:
  maximum:
    DOB: age_band_from_dob       # override: at maximum, use band not exact age
```

---

## Audit Events

All policy engine actions are logged to `data/audit.jsonl`. Raw PHI is never logged.

| Event | Logged fields |
|-------|--------------|
| `POLICY_PROFILE_SELECTED` | task, profile, strictness |
| `POLICY_PREPARATION_STARTED` | task, consumer_type, provider_risk |
| `POLICY_TRANSFORMATION_APPLIED` | entities_processed, strictness |
| `POLICY_USEFULNESS_CHECKED` | score, passes, task |
| `POLICY_RISK_CHECKED` | risk_score |
| `POLICY_STRICTNESS_SELECTED` | selected, mode |
| `POLICY_PACKAGE_CREATED` | package_id, recommended_action |
| `POLICY_PACKAGE_EXPORTED` | package_id, zip_path |

---

## Zone 1 Security Guarantees

The following are **never** included in:
- prepared package ZIP
- Zone 2 export
- audit log entries
- LLM prompt payloads

| Secret | Location |
|--------|----------|
| `policy_token_map` | PreparedPackage field — in-memory Zone 1 only |
| `original_text` on TransformationEntry | Stripped before package write |
| original extracted markdown | Never passed to Zone 2 |
| original PDF | Never stored in prepared_packages/ |

The `_assert_no_token_leak()` function in `package.py` enforces this programmatically after every save.

---

## Tests

84 passing tests across:

| Module | Coverage |
|--------|----------|
| `test_profiles.py` | Profile loading, validation, defaults, thresholds, weights |
| `test_transformer.py` | All actions, dismissed/pending handling, token stability, DOB date |
| `test_strictness.py` | Escalation table, provider floor, rehydration_required |
| `test_usefulness.py` | Weighted scoring, check detection, threshold comparison |
| `test_risk.py` | Direct/quasi checks, warnings, risk scoring |
| `test_package.py` | Package files, checksums, no mapping leak, max_allowable |
| `test_reports.py` | Enriched reports, preview.md, Zone 1 security, PHI absence |

Run with:
```bash
cd /Users/vmac/PycharmProjects/upworkProjects/confidoc
uv run pytest tests/policy_engine/ -v
```
