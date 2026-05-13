# Confidoc — Next Architecture Stage

## Core Principle

The system must separate four distinct concerns, each with its own access boundary:

| Concern | Who touches it | What they see |
|---|---|---|
| Sensitive identity data | Compliance reviewer only | Real names, dates, addresses |
| Linguistic/content correction | OCR/linguistic reviewer | Pseudonymized text only |
| Translation/export | Translator | Pseudonymized TMX/CSV |
| Final reconstruction | Authorized approver only | Mapping store + translated doc |

Linguists and translators must never see patient identities. Reconstruction is a
privileged, audited operation — not an automatic step.

---

## Updated Pipeline

```
PDF
 │
 ▼
[1. Extraction]          Gemini → extracted markdown
 │
 ▼
[2. Entity detection]    Regex + LLM → entity list
 │
 ▼
[3. HITL anon review]    Compliance reviewer approves/edits entities
 │                       Stable token map generated here (see §2)
 ▼
[4. Pseudonymized MD]    "[PATIENT_NAME_001]", "[DATE_003]" etc.
 │                       Mapping stored encrypted, separate from content
 ▼
[5. Linguistic review]   OCR correction, structural cleanup, CAT preview
 │                       Reviewer sees pseudonymized text ONLY
 ▼
[6. Export]              TMX / CSV / Markdown — pseudonymized throughout
 │
 ▼
[7. Translation]         External translator works on pseudonymized segments
 │
 ▼
[8. Controlled rehydration]   Privileged user + encrypted mapping → final doc
 │
 ▼
[9. Final document]      Reconstructed, translated, patient-ready output
```

---

## 1. Stable Token Mapping System  *(Priority 1)*

### Why stability matters

The current implementation replaces entity text with generic tokens (`[PATIENT_NAME]`,
`[DATE]`). This is sufficient for single-document anonymization review but breaks
reconstruction because:

- The same patient name appearing 8 times is indistinguishable from 8 different patients.
- After translation, there is no way to rehydrate specific identities.
- Audit traceability requires knowing which token corresponded to which identity.

### Proposed token scheme

Numbered, label-scoped tokens assigned once per job at approval time:

```
[PATIENT_NAME_001]   → "Phoung Eliopoulos"
[PHYSICIAN_NAME_001] → "Zamperoni"
[DATE_001]           → "11.10.2023"
[DATE_002]           → "08.07.1972"
[ADDRESS_001]        → "65191 Wiesbaden, Westfalenstr. 10"
```

All occurrences of the same text get the same numbered token — enabling correct
rehydration even when a name appears in every paragraph.

### Mapping storage

```
data/mappings/
  {job_id}.enc.json      encrypted mapping file, never exported
```

Mapping file structure (before encryption):

```json
{
  "job_id": "31d832c2c9...",
  "created_by": "reviewer_id",
  "created_at": "2026-05-12T16:40:00Z",
  "tokens": {
    "[PATIENT_NAME_001]": "Phoung Eliopoulos",
    "[PHYSICIAN_NAME_001]": "Zamperoni",
    "[DATE_001]": "11.10.2023",
    "[DATE_002]": "08.07.1972",
    "[ADDRESS_001]": "65191 Wiesbaden, Westfalenstr. 10"
  }
}
```

**Mappings must never appear in exported markdown, TMX, or CSV.**

### Encryption approach

- Each job gets a unique symmetric key (AES-256-GCM).
- Keys are stored separately from mapping files — ideally in a secrets manager
  (environment variable, AWS KMS, HashiCorp Vault) rather than on disk.
- Minimum viable: key stored in env / `.env.keys` with restricted file permissions,
  separate from the mapping data.
- Scale is not a concern: even 10 million mappings across years is trivial in
  SQLite or PostgreSQL. The important properties are encryption, access control,
  and audit — not storage size.

### Implementation tasks

- [ ] Add `data/mappings/` directory and config path
- [ ] Add `app/storage/mappings.py` — write/read/encrypt/decrypt
- [ ] Assign stable numbered tokens during HITL approval (replace current generic tokens)
- [ ] Strip mapping from all export outputs (assert no `[LABEL_NNN]` appears without a corresponding mapping entry)

---

## 2. Controlled Rehydration  *(Priority 2)*

Rehydration reconstructs the final patient-visible document from the translated
pseudonymized content and the encrypted mapping store. It is a privileged, logged
operation — not triggered automatically.

### Rehydration inputs

```
translated pseudonymized markdown   (from translator)
+ encrypted mapping store           (from compliance reviewer's job)
+ authorized user credential
= final reconstructed document
```

### Permission model

Three roles, strictly separated:

| Role | Can do | Cannot do |
|---|---|---|
| **Translator** | View pseudonymized docs, edit markdown, export TMX | See mappings, rehydrate |
| **Compliance Reviewer** | Approve anonymization, manage mappings | Translate, export final docs |
| **Final Approver** | Rehydrate document, generate patient-visible output | (no additional restriction) |

### Implementation tasks

- [ ] Add `role` field to user/session model
- [ ] `POST /api/jobs/{job_id}/rehydrate` — restricted to `final_approver` role
- [ ] Decrypts mapping for job, performs token substitution on translated markdown
- [ ] Writes `data/final/{job_id}_{stem}_final.md` (and optionally PDF)
- [ ] Audit event `REHYDRATION_PERFORMED` with actor, timestamp, job_id

---

## 3. Linguistic Normalization Review  *(Priority 3)*

Placement: **after anonymization, before export**.

The linguistic reviewer corrects OCR artefacts and structural issues in the
pseudonymized markdown. They never see patient identities.

### Observed OCR issues in current documents

| Raw OCR text | Expected |
|---|---|
| `vergoBerter` | `vergrößerter` |
| `Sonokontrollein6Monaten` | `Sonokontrolle in 6 Monaten` |
| `Sehir geehrte` | `Sehr geehrte` |
| `Ov:Zyste.rechts` | `Ov-Zyste rechts` |
| `gelegentlicher:` | `gelegentlicher` |

### Review UI features

The markdown editor becomes a second-stage review interface:

- Raw markdown editing with syntax awareness
- Side-by-side rendered preview
- OCR artefact highlighting (custom heuristics + optional LanguageTool)
- Medical dictionary spellcheck (Hunspell + DIMDI/medical wordlists)
- Suspicious token highlighting (digit-glued-to-word, unexpected punctuation mid-word)
- Whitespace normalization
- Section heading cleanup
- Paragraph splitting / joining
- CAT segmentation preview (shows how the text will segment for TMX)
- Optional Gemini cleanup suggestions (operating on pseudonymized text only)

### OCR heuristics to implement

```python
# Digit glued into word
re.compile(r'[a-zäöü]\d+[a-zäöü]', re.I)          # "in6Monaten"

# Unexpected punctuation mid-word
re.compile(r'[a-zäöü][:.]{1}[a-zäöü]', re.I)       # "Ov:Zyste", "rechts.AuBer"

# Likely OCR substitution (common confusables)
# B→ß, rn→m, li→h, O→0, I→l
```

### Implementation tasks

- [ ] New job status: `normalizing` (between `reviewing` and `approved`)
- [ ] `GET /api/jobs/{job_id}/md` already exists — extend to support PUT for edits
- [ ] `app/pipeline/normalize.py` — run heuristics, flag suspicious spans
- [ ] `GET /api/jobs/{job_id}/ocr_flags` — returns flagged positions for UI highlighting
- [ ] Linguistic review UI pane (second tab in detail view, or separate route)
- [ ] Audit event `MARKDOWN_EDITED` with line-level diff

---

## 4. Audit Layer  *(Priority 5)*

Audit is cross-cutting infrastructure — it must cover every stage.

### Current audit events (already implemented)

```
job_created          entity_detected       entity_approved
entity_dismissed     entity_edited         all_entities_approved
entity_added_manual  redetect              llm_anon_done
review_applied       export_done           job_created (input_dir)
```

### Audit events to add

```
MAPPING_CREATED          when stable token map is written
MAPPING_ACCESSED         every read of the encrypted mapping (who, when)
MARKDOWN_EDITED          line-level diff, reviewer identity
NORMALIZATION_STARTED    linguistic review begins
NORMALIZATION_COMPLETE   linguistic review submitted
TRANSLATION_IMPORTED     translated TMX/segments received
REHYDRATION_PERFORMED    who reconstructed which job, timestamp
EXPORT_DOWNLOADED        who downloaded which export file
```

### Audit properties every event should carry

```json
{
  "ts":        "ISO-8601 UTC",
  "job_id":    "...",
  "actor":     "user_id or 'system'",
  "role":      "compliance_reviewer | translator | final_approver | system",
  "event":     "REHYDRATION_PERFORMED",
  "detail":    { "...": "..." }
}
```

The existing `audit.jsonl` append-only log is the right foundation. For production,
forward to a tamper-evident log (CloudTrail, immutable S3, Datadog, etc.).

### Why audit makes the product

| Without audit | With audit |
|---|---|
| Useful anonymization tool | Compliance-capable document workflow platform |
| "We think identities were protected" | "We can prove identities were protected" |
| Cannot investigate incidents | Full reconstruction accountability |
| Cannot support outsourcing | Safe to send pseudonymized content to external translators |
| HIPAA/GDPR: best effort | HIPAA/GDPR: demonstrable controls |

---

## 5. Role-Based Permissions  *(Priority 6)*

Currently there are no user sessions. For multi-user deployment:

- Add session token / JWT middleware (FastAPI dependency)
- `role` injected into every route — checked at handler level
- Audit `actor` field populated from session
- `/api/jobs/{job_id}/rehydrate` — 403 if role ≠ `final_approver`
- `/api/jobs/{job_id}/mappings` — 403 if role ≠ `compliance_reviewer | final_approver`
- Export routes — 403 if role = `final_approver` only (they reconstruct, not distribute)

Minimum viable: HTTP Basic Auth with a roles config file.
Production: OAuth2 / SAML with your organisation's IdP.

---

## Development Priorities

| Priority | Feature | Why first |
|---|---|---|
| 1 | Stable token mapping + encrypted storage | Blocks rehydration; current tokens lose identity on multi-occurrence |
| 2 | Rehydration pipeline | Core value proposition — without it, the pipeline is one-way |
| 3 | Markdown normalization review UI | Improves TMX quality and translator efficiency before export |
| 4 | OCR/spellcheck heuristics | Reduces manual editor effort; can ship incrementally |
| 5 | Full audit event coverage | Required for compliance positioning |
| 6 | Role-based permissions | Required for multi-user / outsourced-translator deployment |

---

## What the current implementation already has

- PDF → Gemini extraction → markdown
- Regex PII detection (dates, addresses, names, physician names, postal codes, IDs, phones)
- LLM second pass (Gemini, privacy-safe: sends partially-masked text only, no raw PII)
- HITL review browser UI — approve / dismiss / edit / add manual entities
- Manual entity addition via text selection in the preview
- Re-detect preserving manual entities
- Export → TMX + CSV + redacted markdown
- `approved_terms.jsonl` feedback log feeding the LLM few-shot guidance
- Append-only `audit.jsonl` for all current events
- Input-directory pickup (drop PDFs in `data/input/`, process without browser upload)
