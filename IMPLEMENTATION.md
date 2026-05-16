# Confidoc — Implementation Notes

Secure document pipeline for pseudonymising confidential (primarily German medical) PDFs
before translation export or downstream processing. Built on top of the `pdf-to-markdown` library.

---

## Architecture

```
PDF upload  (or artifact import at any stage)
    │
    ▼
[1. ingest]       Gemini 2.0 Flash — PDF pages → extracted markdown
    │
    ▼
[2. anon]         Regex pass — dates, addresses, postcodes, names, phones …
    │
    ▼
[3. anon_llm]     LLM pass — Gemini on partially-masked text, few-shot from
    │             approved_terms.jsonl; catches names/institutions regex missed
    ▼
[4. HITL review]  Browser UI — approve / dismiss / edit / add manual entities
    │             Re-detect preserves manual entities across pattern updates
    ▼
[5. export]       Stable token assignment → pseudonymized markdown
    │             Encrypted mapping saved → data/mappings/{job_id}.enc
    │             approved_terms.jsonl updated (generic labels, no raw PII)
    │             TMX + CSV written from pseudonymized text
    ▼
[6. OCRCheck]     Normalization review — token-aware OCR detection + markdown editor
    │             Reviewer corrects artefacts; auto-fix applies unambiguous suggestions
    │             Stable tokens validated on every save — cannot be deleted or added
    ▼
[7. export]       Re-export using normalized_md if available, else falls back to reviewed_md
    ▼
[Translation]     External TMX/CSV handed to translator (pseudonymized throughout)
    ▼
[Rehydration]     Encrypted mapping + authorized user → final reconstructed document


         ─── or, instead of/after export ───

    ▼
[8. Policy Engine]  Task-specific anonymization package for Zone 2 consumers
                    (translators, LLMs, researchers, public release)
                    Profile-driven rules + strictness + usefulness/risk scoring
                    Output: prepared.md + manifest/reports ZIP — no PHI, no mapping
```

### Zone Architecture (Policy Engine)

```
Zone 1: Identifiable Source Zone
  original PDF · extracted Markdown · approved entities
  encrypted token mapping · source backup

        ↓  PolicyRequest (task · profile · strictness · consumer type)

  Confidoc Policy Engine
  entity transformation rules · usefulness checks · risk checks

        ↓  PreparedPackage (no PHI, no mapping)

Zone 2: Anonymized Processing Zone
  human translator · LLM · external researcher · vendor

        ↓  (authorized rehydration only)

Zone 3: Controlled Rehydration Zone
  authorized clinician/HCP · decrypt mapping · restore identifiers
```

**Critical rule:** Zone 2 never receives the original PDF, original text, or decrypted token mapping.

Entry points — any saved artifact can re-enter at the correct stage:

| Artifact | Enters at |
|---|---|
| PDF | Ingest |
| `extracted.md` | Entity review |
| `pseudonymized_reviewed.md` (has `[LABEL_NNN]` tokens) | OCRCheck |
| `normalized.md` | Export |

---

## Job statuses

```
pending → extracting → reviewing → approved → normalizing → normalized → exporting → done
                                          ↘ (skip normalization)  ↗
                                                                  
failed  (can occur at any stage)
```

---

## Pipeline stages

### 1. Ingest (`app/pipeline/ingest.py`)

- Saves the uploaded PDF to `data/input/`
- Calls `pdf_to_markdown.pipeline.run_batch()` — Gemini 2.0 Flash reads each page
  as an image and returns structured Markdown
- Concurrency: `MAX_CONCURRENT_PDFS` / `MAX_CONCURRENT_PAGES` (env vars)
- On success: writes `data/extracted/{stem}.md`, updates status → `reviewing`

### 2. Regex PII detection (`app/pipeline/anon.py`)

Fast, deterministic, no external calls. Patterns run in priority order.

| Label | What it matches |
|---|---|
| `CASE_ID` | Numbers after "Fallnummer:", "Case", "Aufnahme-Nr." |
| `DATE` | `DD.MM.YYYY`, `DD/MM/YYYY`, ISO `YYYY-MM-DD` |
| `ADDRESS` | Full `PLZ City, Streetstr. N` • `Hauptstraße 12` • abbreviated `str.` • US-style |
| `LOCATION` | German 5-digit postcode + city (hyphenated names; OCR-lowercase after hyphen handled) |
| `PATIENT_NAME` | Names after "Patientin", "Name:", "Vorname:", "geb." |
| `PHYSICIAN_NAME` | Surnames after "Mit freundlichen Grüßen" / "MfG" — tolerates OCR period variants |
| `ID_NUMBER` | `[A-Z]\d{9,12}` |
| `PHONE` | International and local formats |

Key details:

- Patterns with a capture group use `m.group(1)` — contextual prefix (e.g. "Patientin")
  is not part of the entity text or replacement.
- No `re.I` on name patterns — prevents common words like "Patientin kam" matching as names.
- Subsumption check: a span fully inside an already-accepted span is dropped
  (e.g. `65191` suppressed when `65191 Wiesbaden` is already captured).

### 3. LLM PII detection (`app/pipeline/anon_llm.py`)

Second pass — catches novel names, institutions, and patterns regex cannot.

**Privacy model:**

1. Regex-detected entities are applied to create a **partially-masked** document.
   `[PATIENT_NAME]`, `[DATE]`, `[ADDRESS]` replace known PII before any API call.
2. Only the masked document is sent to Gemini.
3. Few-shot guidance = label-type descriptions + per-label confirmation count from
   `approved_terms.jsonl`. **No actual PII values are transmitted.**
4. Gemini returns verbatim strings found in the masked text.
5. Strings are located in the **original** unmasked text to recover character offsets.

Residual exposure: PII missed by regex is still visible to Gemini (unavoidable —
it must read text to flag it). The design limits exposure to genuinely novel cases only.

### 4. HITL anonymization review (browser UI)

**Entity panel:**
- Each detected entity card: label badge, original text, proposed replacement
- Approve / Dismiss / Edit replacement (per entity)
- Approve All button
- `dismissed: bool` field distinguishes "explicitly rejected" from "not yet reviewed"

**Dismissed entity behaviour:**
- Dismissed entities are not tokenized and not written to the mapping
- Their `replacement` field is set to the original text (identity — no substitution)
- Generic labels like `[PATIENT_NAME]` are never written to export outputs

**Adding missed entities:**
- Select any text in the extracted-text preview → floating label picker
- Choose label, confirm or customise the replacement token
- Saved with `manual: true`; highlighted in green with dashed border
- Survives Re-detect: merged back after auto passes, dropped only if auto now covers
  the exact same span

**Re-detect:**
- Reruns regex + LLM passes without re-running Gemini on the PDF
- Stashes manual entities → re-runs detection → merges manuals back

### 5. Export (`app/pipeline/export.py`)

Triggered by the reviewer after anonymization approval (or after normalization).

**Token assignment (`app/storage/mappings.py`):**

Two strategies, applied at export time:

| Label type | Strategy |
|---|---|
| Identity labels (names, addresses, IDs, phones, locations) | Same text → same token across the job |
| `DATE` | Occurrence-based — each span gets its own token regardless of date string |

Rationale for date occurrence-based: the same date string can be report date, birth date,
admission date — they carry different semantic roles and must be independently rehydratable.

Example:
```
[PATIENT_NAME_001]   → "Phoung Eliopoulos"  (all occurrences share this token)
[PHYSICIAN_NAME_001] → "Zamperoni"
[DATE_001]           → "11.10.2023"          (report date, 1st occurrence)
[DATE_002]           → "11.10.2023"          (2nd occurrence — different role)
[DATE_003]           → "08.07.1972"          (birth date)
[ADDRESS_001]        → "65191 Wiesbaden, Westfalenstr. 10"
```

**Export steps:**

1. Log approved entities to `approved_terms.jsonl` using generic labels (before tokenization,
   so `[PATIENT_NAME]` not `[PATIENT_NAME_001]` is recorded — keeps the LLM guidance clean)
2. Assign stable numbered tokens via `assign_tokens()`
3. Apply tokenized replacements (right-to-left on offsets) → `data/reviewed/{stem}_reviewed.md`
4. Encrypt and save token mapping → `data/mappings/{job_id}.enc`
5. Produce TMX + CSV from the pseudonymized markdown
6. Source selection: `normalized_md` if available, else `reviewed_md`, else `extracted_md`

**Mapping file:**
- Fernet-encrypted (AES-128-CBC + HMAC-SHA256)
- Key from `MAPPING_KEY` env var; auto-generated dev key written to `data/mappings/.key`
  with a warning if env var is not set
- File permissions `0o600`
- Never included in exports

### 6. OCRCheck / Markdown normalization (`app/pipeline/ocr_check.py`)

Runs **after** pseudonymization. The reviewer edits the pseudonymized markdown only —
never raw PHI.

**Detection rules (all skip stable token spans):**

| Rule | Example caught |
|---|---|
| `B_AS_SS` | `vergoBerter` → `vergrößerter`, `GroBe` → `Große` |
| `DIGIT_IN_WORD` | `Sonokontrollein6Monaten` |
| `OCR_SEHIR` | `Sehir` → `Sehr` |
| `OCR_GRUBEN` | `Gruben` → `Grüßen` |
| `OCR_FUR` | `fur` → `für` |
| `MISSING_SPACE_SENTENCE` | `Befund.Die` → `Befund. Die` |
| `PUNCT_IN_WORD` | `Ov:Zyste.rechts` |
| `TRAILING_WHITESPACE` | trailing spaces/tabs |
| `EXCESS_BLANK_LINES` | more than two consecutive blank lines |

**Token integrity:**
- Every save validated by `validate_tokens()` — compares multisets of `[LABEL_NNN]` tokens
  before and after edit; rejects if any token was added, removed, or changed
- Auto-fix (`apply_suggestions`) applies unambiguous suggestions right-to-left and also
  passes token validation

**UI (OCR Check tab):**
- Left pane: editable `<textarea>` — raw pseudonymized markdown
- Right pane: flag list with severity, message, suggested fix, "Apply" button
- Clicking a flag scrolls the textarea to the flagged position
- Auto-save debounced at 1.8 s of inactivity
- ⚡ Auto-fix button applies all unambiguous corrections at once
- ✓ Approve Normalized locks the content and sets status → `normalized`

### 7. Rehydration (`POST /api/jobs/{job_id}/rehydrate`)

Privileged operation. Decrypts the job's mapping file, substitutes every stable token
with its original value, writes `data/final/{stem}_final.md`.

Audit event `REHYDRATION_PERFORMED` logged with actor and token count.

In production this endpoint must be restricted to the `final_approver` role
(role-based permissions not yet implemented — see `NEXT_STAGE.md`).

---

### 8. Policy Engine (`app/policy_engine/`)

The Policy Engine sits between the identifiable source (Zone 1) and any downstream
consumer (Zone 2). It is available from the **🔒 Policy** tab on any reviewed, normalized,
or done job.

Full documentation: [`POLICY_ENGINE.md`](POLICY_ENGINE.md)

#### Core components

| Module | Responsibility |
|---|---|
| `engine.py` | `prepare()` entry point; routes `max_allowable` vs single-strictness runs |
| `profiles.py` | Loads YAML profiles from `profiles/`; returns `ProfileConfig` |
| `transformer.py` | Applies transformation actions to each approved entity |
| `strictness.py` | Escalation table; provider risk floor; `resolve_action()` |
| `usefulness.py` | 18 deterministic weighted checks; compares score to profile threshold |
| `risk.py` | 9 re-identification risk checks; produces direct/quasi/overall risk score |
| `package.py` | Writes prepared package files; `_assert_no_token_leak()` security assertion |
| `models.py` | `PolicyRequest`, `PreparedPackage`, `TransformationEntry`, `MaxAllowableDecision` |

#### Transformation actions

| Action | Output | Example |
|---|---|---|
| `stable_token` | Numbered placeholder | `[PATIENT_001]` |
| `remove` | Label placeholder | `[REMOVED_ADDRESS]` |
| `age_from_dob` | Exact age | `age 51` |
| `age_band_from_dob` | 10-year band | `age band 50-59` |
| `relative_date` | Day offset or numbered token | `Day -30` / `[DATE_001]` |
| `coarse_relative_date` | Week offset | `[~-4w from report]` |
| `generalize` | Region or country | `Bayern` / `[REGION]` |
| `date_shift` | Consistent date shift per job | |
| `keep` | Original text preserved | (clinical content) |
| `flag_for_review` | Original text + risk warning | (quasi-identifier) |

DOB reference date priority: `REPORT_DATE` entity → `document_date` from request → today.

#### Strictness levels

| Level | Behaviour |
|---|---|
| `minimal` | Direct identifiers tokenized; most content preserved |
| `balanced` | Profile default rules applied |
| `strict` | `keep` → `flag_for_review` for quasi-identifiers |
| `maximum` | `stable_token` → `remove` (unless `rehydration_required: true`) |
| `max_allowable` | Tries maximum → strict → balanced → minimal; picks strictest passing level |

Provider risk floor: `cloud_llm` and `external_researcher` → minimum `strict`;
`public` → minimum `maximum`; `trusted_internal` → `minimal` allowed.

#### Prepared package

Every `prepare()` call writes to `data/prepared_packages/<package_id>/`:

```
prepared.md               ← anonymized document for Zone 2
manifest.json             ← summary, counts, decision, recommendation, checksums
risk_report.json          ← direct/quasi risk assessment
usefulness_report.json    ← weighted check breakdown
transformation_log.json   ← what was done to each entity (no original PHI)
preview.md                ← human-readable summary for UI rendering
```

`_assert_no_token_leak()` runs after every write and raises `RuntimeError` if any
Zone 1 secret (token map, original text, raw PHI) appears in any package file.

#### Profiles (`profiles/`)

| Profile | Task | Default strictness | Usefulness threshold |
|---|---|---|---|
| `translation.yaml` | translation | balanced | 0.78 |
| `clinical_summary.yaml` | clinical_summary | strict | 0.90 |
| `ml_feature_extraction.yaml` | ml_feature_extraction | strict | 0.70 |
| `research_extract.yaml` | research_extract | maximum | 0.60 |
| `public_release.yaml` | public_release | maximum | 0.40 |

`rehydration_required: true` (set on translation profile) preserves `stable_token`
even at `maximum` strictness so downstream translators can rehydrate.

#### API endpoints

| Endpoint | Purpose |
|---|---|
| `POST /api/policy/prepare` | Run engine, returns safe summary (no PHI, no mapping) |
| `GET /api/policy/packages/{id}/report` | Full report JSON + preview.md |
| `GET /api/policy/packages/{id}/download` | Stream ZIP (Zone 2 safe) |

`_safe_policy_response()` explicitly excludes `policy_token_map` and all Zone 1 fields
before returning JSON to the browser.

#### UI (🔒 Policy tab)

Available on jobs with status `reviewing`, `approved`, `normalizing`, `normalized`, or `done`.

Left column — form:
- Task selector (translation / clinical_summary / ml_feature_extraction / research_extract / public_release)
- Strictness selector (max_allowable / balanced / strict / maximum / minimal)
- Consumer type (auto-derives provider risk)
- **Prepare Package** button

Right column — results (rendered after prepare):
- Recommendation badge (Approved / Review Required / Reject) with color coding
- Warning banner when `review_required` or `reject`
- Risk card: overall / direct / quasi scores + warnings
- Usefulness card: score bar + per-check weighted breakdown table
- Strictness selection table (max_allowable mode only)
- Transformation summary (action counts)
- Preview.md rendered via marked.js
- Download Package ZIP button
- Zone 1 security notice

---

## Artifact import (`POST /api/import`)

Any pipeline artifact can be imported as a new resumable job:

| `stage` parameter | File type | Job created at |
|---|---|---|
| `extracted` | `.md` (raw extracted) | `reviewing` |
| `pseudonymized` | `.md` (has `[LABEL_NNN]` tokens) | `normalizing` |
| `normalized` | `.md` (post-OCRCheck) | `normalized` (ready to export) |

Validation: pseudonymized imports must contain at least one `[LABEL_NNN]` token.
Pseudonymized imports automatically trigger an OCRCheck background pass.

---

## Feedback loop (`data/approved_terms.jsonl`)

Append-only JSONL written at export time. One line per approved entity:

```json
{
  "ts": "2026-05-12T16:40:18Z",
  "job_id": "31d832c2c9...",
  "filename": "GYN_report_…pdf",
  "label": "PHYSICIAN_NAME",
  "text": "Zamperoni",
  "replacement": "[PHYSICIAN_NAME]",
  "manual": false
}
```

The LLM pass reads this on every run and builds a guidance block:

```
PHYSICIAN_NAME (1 confirmed so far): doctor or physician surnames,
  often after a letter closing such as 'Mit freundlichen Grüßen'
DATE (2 confirmed so far): dates in any format …
```

No actual PII values are sent to Gemini — only label descriptions and counts.
As more jobs are exported the counts grow, reinforcing which entity types are
relevant to this document class.

---

## Data directories

```
data/
  input/              raw uploaded PDFs
  extracted/          Gemini-extracted markdown
  anonymized/         (reserved — future pre-review snapshots)
  reviewed/           pseudonymized markdown (stable tokens applied)
  normalized/         OCRCheck-corrected pseudonymized markdown
  exported/           TMX and CSV translation exports
  final/              rehydrated final documents (post-translation)
  jobs/               one JSON file per job (full state machine)
  mappings/           encrypted per-job token maps ({job_id}.enc)
  prepared_packages/  Zone 2 prepared packages (one dir per package_id)
                        prepared.md · manifest.json · risk_report.json
                        usefulness_report.json · transformation_log.json
                        preview.md · (optional) package.zip
  audit.jsonl         append-only event log (pipeline + policy engine)
  approved_terms.jsonl  PII feedback log — feeds LLM few-shot pass
```

---

## Audit events

### Pipeline events

| Event | When |
|---|---|
| `job_created` | New job from upload or input-dir pick |
| `JOB_IMPORTED` | Job created from artifact import |
| `extraction_started/done/failed` | Ingest stage |
| `anon_detected` | Regex pass complete |
| `llm_anon_done` / `llm_anon_failed` | LLM pass complete or failed |
| `entity_approved` / `entity_dismissed` / `entity_edited` | Per-entity reviewer action |
| `entity_added_manual` | Reviewer added entity via text selection |
| `all_entities_approved` | Approve All clicked |
| `redetect` | Re-detect run (auto + manual counts logged) |
| `review_applied` | Export tokenization applied |
| `MAPPING_CREATED` | Encrypted mapping file written |
| `MAPPING_ACCESSED` | Mapping preview read (audit trail) |
| `EXPORT_GENERATED` | TMX + CSV written |
| `OCRCHECK_STARTED` | Normalization stage entered |
| `OCRCHECK_EDITED` | Normalized markdown draft saved |
| `OCRCHECK_AUTOFIX` | Auto-fix applied |
| `OCRCHECK_APPROVED` | Normalization approved |
| `REHYDRATION_PERFORMED` | Final document reconstructed |

### Policy Engine events

| Event | Logged fields |
|---|---|
| `POLICY_PROFILE_SELECTED` | task, profile, strictness |
| `POLICY_PREPARATION_STARTED` | task, consumer_type, provider_risk |
| `POLICY_TRANSFORMATION_APPLIED` | entities_processed, strictness |
| `POLICY_USEFULNESS_CHECKED` | score, passes, task |
| `POLICY_RISK_CHECKED` | risk_score |
| `POLICY_STRICTNESS_SELECTED` | selected, mode |
| `POLICY_PACKAGE_CREATED` | package_id, recommended_action |
| `POLICY_PACKAGE_EXPORTED` | package_id, zip_path |

Raw PHI is never logged in any event.

---

## Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `GOOGLE_API_KEY` | — | Gemini API key (required) |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Model for PDF extraction and LLM PII pass |
| `MAX_CONCURRENT_PDFS` | `3` | Parallel PDF jobs during ingest |
| `MAX_CONCURRENT_PAGES` | `5` | Parallel page calls per PDF |
| `MAPPING_KEY` | auto-generated | Fernet key for encrypting token mapping files |
| `HOST` | `127.0.0.1` | Uvicorn bind address |
| `PORT` | `8100` | Uvicorn port |

---

## Running

```bash
uv run confidoc
# → http://127.0.0.1:8100
```

Uvicorn runs with `reload=True` — file changes hot-reload immediately.

PDFs: drop into `data/input/` and pick from the sidebar dropdown, or use the
browser upload form. Any `.md` artifact can be imported via the Import sidebar button.

---

## Key design decisions

**Pseudonymization, not anonymization** — stable numbered tokens are assigned at export
time, enabling full rehydration. Generic labels like `[PATIENT_NAME]` are reserved for
a future irreversible anonymization mode; they do not appear in any export output.

**Regex before LLM** — structural patterns (dates, postcodes, phone numbers) are caught
more reliably by regex. The LLM handles the long tail: novel names, institutions, and
patterns that cannot be expressed as a rule.

**LLM sees only masked text** — PII already caught by regex is replaced with tokens
before the Gemini API call. Few-shot guidance uses label counts and descriptions —
no raw PII values leave the system for already-confirmed entities.

**Date tokens are occurrence-based** — the same date string (e.g. "11.10.2023") may
serve as report date, exam date, or admission date. Each occurrence gets its own token
so downstream reconstruction and audit remain unambiguous.

**Dismissed entities carry no token** — `replacement` is set to the original text
(a no-op), dismissed entities are excluded from the mapping, and the original text
appears unchanged in all outputs.

**Token validation at every normalization save** — the multiset of `[LABEL_NNN]` tokens
must be identical before and after any editor change. This prevents reviewers from
accidentally breaking the rehydration mapping during OCR correction.

**Approved terms log before tokenization** — `approved_terms.jsonl` is written using
generic labels (`[PATIENT_NAME]`, not `[PATIENT_NAME_001]`) so the LLM few-shot guidance
stays clean across jobs and doesn't accumulate numbered tokens.

**Non-linear, resumable pipeline** — every stage writes a named artifact to a stable
path. Any artifact can be imported to create a new job at the correct stage, enabling
out-of-order delivery, re-processing after improved patterns, and safe handoff between
teams.

**Manual entities survive re-detect** — reviewers invest time adding missed spans;
those survive pattern updates and LLM re-runs unless the auto pass now covers the
exact same character span.

**Policy Engine is a separate layer, not a replacement for pseudonymization** — the
pipeline (stages 1–7) produces a pseudonymized document with a fully rehydratable
mapping. The Policy Engine (stage 8) is an independent preparation step that decides
what to share with a specific consumer for a specific task. The two layers serve
different purposes: the pipeline preserves all information under controlled access;
the Policy Engine minimises information for downstream use.

**Zone 1 security is enforced programmatically** — `_assert_no_token_leak()` in
`package.py` reads every file written to a prepared package and raises `RuntimeError`
if any token map value, original entity text, or other Zone 1 secret appears.
The `_safe_policy_response()` route helper enforces the same constraint at the API
boundary — `policy_token_map` and `prepared_text` are explicitly excluded from all
JSON responses, not just omitted by convention.

**max_allowable iterates from strictest to least strict** — the engine tries maximum →
strict → balanced → minimal and returns the first level whose usefulness score meets
the profile threshold. This guarantees the consumer receives the most aggressively
anonymized document that still serves the task, without requiring the operator to
guess which level to use.

**rehydration_required preserves stable_token at maximum strictness** — for translation
workflows the downstream translator must be able to map tokens back to originals.
Setting `rehydration_required: true` in the profile exempts `stable_token` entities
from the `maximum` escalation that would otherwise convert them to `remove`.

**Usefulness checks are deterministic, not LLM-based** — scores are computed from
regex and structural checks (diagnoses present, dates present, enough text, section
headers intact, etc.) to keep the Policy Engine fast, auditable, and free of external
API calls at preparation time.

**Tests: 84 passing across 7 modules** — see `POLICY_ENGINE.md` for the full test
matrix. Run with:
```bash
cd /Users/vmac/PycharmProjects/upworkProjects/confidoc
uv run pytest tests/policy_engine/ -v
```
