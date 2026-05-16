# Confidoc — Policy Engine

The Policy Engine controls how a Zone 1 pseudonymized document is transformed before it leaves Zone 1 for a specific downstream use case. It answers the question: *"Given who will use this document and for what purpose, what is the safest and most useful version we can produce?"*

---

## Overview

```
Zone 1 document (pseudonymized)
        │
        ▼
PolicyRequest { job_id, task, strictness_mode, consumer_type, provider_risk }
        │
        ├─► Profile loading           (which entity rules apply to this task?)
        ├─► Strictness resolution     (what is the effective strictness floor?)
        ├─► Transformation            (apply rules entity by entity)
        ├─► Usefulness scoring        (is the result still useful?)
        ├─► Risk assessment           (does residual PHI remain?)
        ├─► Max-allowable search      (optional: find strictest passing level)
        └─► Package + ZIP             (Zone 2 safe output files)
```

---

## Entry Point

**`app/policy_engine/engine.py` — `prepare(request, save=True)`**

The single public API call. Takes a `PolicyRequest`, runs the full pipeline, and returns a `PreparedPackage`.

If `strictness_mode = "max_allowable"`, the engine iterates from **strict → balanced → minimal**, running the full pipeline at each level and returning the strictest level that passes the usefulness threshold for the selected profile.

---

## Profiles

Profiles are YAML files in `app/policy_engine/profiles/`. Each profile defines what a specific downstream task needs and how to handle each entity type.

| Profile | Task | Default Strictness | Rehydration |
|---|---|---|---|
| `translation.yaml` | Human translation | balanced | **required** |
| `clinical_summary.yaml` | Clinical report | strict | no |
| `ml_feature_extraction.yaml` | ML training data | strict | no |
| `research_extract.yaml` | Research dataset | maximum | no |
| `public_release.yaml` | Public publication | maximum | no |

### Profile structure

```yaml
name: translation
default_strictness: balanced
rehydration_required: true      # tokens must be resolvable post-translation

entity_rules:
  PATIENT_NAME:
    action: stable_token        # → [PATIENT_001]
  DATE:
    action: stable_token
  ADDRESS:
    action: remove
  DIAGNOSIS:
    action: keep                # preserves clinical content for translator

usefulness:
  threshold: 0.78
  checks:
    sentence_meaning_preserved: 0.30
    terminology_preserved:      0.25
    placeholders_stable:        0.20
    ...

strictness_overrides:
  maximum:
    PATIENT_NAME: remove        # escalate to remove at maximum strictness
```

---

## Strictness Levels

Four levels in ascending order: `minimal → balanced → strict → maximum`.

### Provider risk floors

The provider type imposes a minimum strictness that cannot be overridden downward:

| Consumer / Provider | Minimum strictness |
|---|---|
| `trusted_internal` | minimal |
| `trusted_vendor`, `local_llm` | balanced |
| `cloud_llm`, `external_researcher` | strict |
| `public` | maximum |

**`effective_strictness = max(requested_level, provider_floor)`**

### Global escalation defaults

Each strictness level has default escalations applied to any entity type not explicitly overridden by the profile:

- **balanced**: `remove → flag_for_review`
- **strict**: `keep → flag_for_review`, `stable_token → flag_for_review`
- **maximum**: all actions → `remove` (except `stable_token` when `rehydration_required = true`)

---

## Transformation Actions

Each entity in the approved list receives one of these actions:

| Action | Result |
|---|---|
| `keep` | Entity text left as-is in output |
| `remove` | Entity replaced with `[REMOVED]` |
| `stable_token` | Entity replaced with `[PATIENT_001]` style token; token → original stored in Zone 1 token map |
| `generalize` | Location generalized (city → region → country) |
| `age_from_dob` | Date of birth replaced with calculated age: `Age: 43` |
| `age_band_from_dob` | DOB replaced with age band: `Age band: 40–50` |
| `relative_date` | Date replaced with offset from report date: `Day +14` |
| `coarse_relative_date` | Date replaced with coarser offset: `Week 2` |
| `date_shift` | Date shifted by fixed offset (preserves relative ordering) |
| `flag_for_review` | Entity marked `⚠ [REVIEW: PATIENT_NAME]` — not removed, human must decide |

Transformation is applied right-to-left over character offsets to preserve correctness after earlier substitutions change string length.

---

## Usefulness Scoring

**`app/policy_engine/usefulness.py`** — 16 deterministic heuristic checks, no LLM.

Each check returns pass/fail and has a weight in the profile. Final score:

```
score = Σ (weight_i × 1.0 if check_i passes)
```

The score is compared against the profile threshold (0.40–0.90). If it fails, `max_allowable` mode tries a less strict level.

### Checks (sample)

| Check | What it tests |
|---|---|
| `diagnoses_preserved` | Key diagnosis tokens still present |
| `medications_preserved` | Medication names not removed |
| `measurements_preserved` | Numeric clinical values (e.g. `120/80 mmHg`) intact |
| `placeholders_stable` | `[TOKEN_001]` format tokens present and well-formed |
| `enough_text_remaining` | Document not over-redacted (>50% text remaining) |
| `sentence_meaning_preserved` | Sentences not truncated mid-way |
| `terminology_preserved` | Medical terminology density maintained |
| `no_direct_identifiers` | No obvious residual PII patterns |

---

## Risk Assessment

**`app/policy_engine/risk.py`** — deterministic regex, no LLM.

Checks for residual PHI after transformation in two categories:

**Direct identifiers** (high individual risk):
- Full names, postal addresses, email addresses, phone numbers, insurance IDs, exact dates of birth

**Quasi-identifiers** (combinatorial re-identification risk):
- Rare conditions with clear text, multiple precise dates, postal codes, flagged-but-not-removed entities

Risk is reported as `low / medium / high` with warnings per failed check. Output goes into `risk_report.json` in the package.

---

## Package Output (Zone 2 safe)

Every export produces a directory `data/prepared_packages/{package_id}/` containing:

| File | Contents | PHI? |
|---|---|---|
| `prepared.md` | Transformed document | Never |
| `manifest.json` | Summary, decision, SHA-256 checksums | Never |
| `risk_report.json` | Risk assessment with warnings | Never |
| `usefulness_report.json` | Check scores and threshold result | Never |
| `transformation_log.json` | Entity-by-entity audit (action applied, output token) | Never — original text excluded |
| `preview.md` | Human-readable summary for UI review | Never |

A ZIP is produced excluding any token mapping files. The ZIP is Zone 2 safe.

### Security contract

The package builder runs `_assert_no_token_leak()` before finalising — asserts that none of the original entity text values appear in any output file. If this assertion fails, the export aborts.

The token map (`{token: original_text}`) is **never** written to the package. It lives only in the encrypted `data/mappings/{job_id}.enc` file in Zone 1.

---

## Rehydration

Profiles with `rehydration_required: true` (currently: translation) use `stable_token` for all direct identifiers. A translator receives `[PATIENT_001]` as a placeholder and translates around it. After translation, the Zone 1 operator can rehydrate — substituting original values back using the encrypted mapping file — to produce a final translated document with real names restored.

Profiles without rehydration use `remove` at maximum strictness, producing permanently anonymized outputs with no recovery path.

---

## UI Integration

The Policy Engine tab (`🔒 Policy`) appears in the detail view for jobs in `reviewing` and beyond.

The UI lets the Zone 1 operator:
1. Select a **task** (translation, clinical summary, ML extraction, research, public release)
2. Select a **strictness mode** (fixed level or max-allowable)
3. Select a **consumer type** (determines provider risk floor)
4. Select a **provider risk** level
5. Run the engine → view risk report, usefulness score, and transformation log
6. Download the safe ZIP package

---

## Domain-Specific Seeds (planned)

Currently the initial entity detection (`app/pipeline/anon.py`) and the picker label list are hardcoded for German medical documents. Planned extensions:

| Vertical | New entity types | Regex seed | LLM prompt |
|---|---|---|---|
| Medical (current) | DIAGNOSIS, MEDICATION, INSURANCE_ID, PATIENT_NAME | German dates, addresses, names | Medical PII prompt |
| Legal | CASE_NUMBER, COURT, PARTY_NAME, LAW_FIRM, JUDGE | Docket formats, case references | Legal PII prompt |
| Financial | IBAN, ACCOUNT_NUMBER, TAX_ID, COMPANY_NAME, AMOUNT | IBAN, tax ID formats | Financial PII prompt |
| HR | EMPLOYEE_ID, SALARY, PERFORMANCE_RATING, MANAGER | HR record patterns | HR PII prompt |

Each vertical will have its own `data/seeds/{domain}/` directory with regex patterns and LLM few-shot examples, loaded at job creation time based on a domain selector in the upload wizard.

---

## Files

```
app/policy_engine/
  engine.py           Main entry point — prepare()
  models.py           PolicyRequest, PreparedPackage, TransformationEntry, ...
  profiles.py         YAML profile loading and validation
  profiles/
    translation.yaml
    clinical_summary.yaml
    ml_feature_extraction.yaml
    research_extract.yaml
    public_release.yaml
  transformer.py      Entity transformation — all action handlers
  strictness.py       Strictness escalation, provider risk floors
  usefulness.py       16-check usefulness scorer
  risk.py             Residual PHI risk assessor
  package.py          Output file builder, ZIP export, PHI leak assertion
  audit.py            Structured audit event logger
```
