# Confidoc — Zone 1 Secure Workflow Specification

## Purpose

This specification defines the next development stage of Confidoc focused on:

- secure Zone 1 workflows
- entity/privacy handling
- controlled LLM export
- reversible anonymization
- secure PDF review UX
- lightweight access control

This document intentionally focuses on Zone 1 only.

---

## Core Principle

Zone 1 is the secure environment.

**Zone 1 contains:**
- original PDFs
- confidential entities
- rehydration mappings
- anonymization controls
- entity learning
- LLM export permissions

**Zone 2 only receives:**
- anonymized outputs
- safe packages
- optionally confidential exports if explicitly allowed

---

## Zone Model

### Zone 1 (Red Zone)

**Contains:**
- raw PDFs
- entity mappings
- rehydration mappings
- confidential names
- medical identifiers
- organization mappings
- manual annotation decisions

**Security assumptions:**
- trusted operator
- local secure access
- restricted users
- audit-oriented

---

### Zone 2 (Safe Processing Zone)

**Contains:**
- anonymized markdown
- safe exports
- LLM-generated reports
- temporary transformed outputs

Zone 2 should **never** permanently store:
- real names
- real organizations
- raw PDFs

…unless explicitly permitted by the user.

---

## UI / UX Changes

### 1. Start Navigation

**Requirement**

Clicking the:
- Confidoc logo
- top-left title
- home icon

must always return to the **Start Screen** without requiring browser back navigation.

---

### 2. PDF Viewer Behaviour

**Current Problem**

PDFs currently open in new tabs. This breaks workflow continuity.

**Required Behaviour**

Clicking a PDF opens a scrollable **popup/modal viewer** inside the app.

**Viewer Requirements**
- scroll vertically through pages
- zoom controls
- close button
- page indicator
- preserve current annotation state

**Technical Suggestion**

Use PDF.js or existing rendered page images.

---

## Entity / Privacy Layer

### Core Philosophy

Confidoc prioritizes:

- **manual control first**
- LLM assistance optional

The more manual the process, the safer the workflow and the lower the privacy risk.

However:
- assisted detection
- adaptive learning
- optional local/private LLMs

…should also be supported.

---

### Current Annotation UI

**Requirement**

The existing manual annotation / selection interface is good. Keep it largely unchanged. This remains the primary **human-in-the-loop anonymization interface**.

---

### Re-Detect Behaviour

**Current Idea**

Pressing **Re-Detect** should not merely rerun static NER. It should improve future detection quality.

**New Requirement**

Re-Detect acts as **incremental entity learning**.

---

### Learning System

**Behaviour**

When users:
- manually select entities
- correct entities
- add/remove tags
- confirm mappings

…the system stores:
- entity patterns
- context examples
- mapping hints
- document-level signals

…for future detection.

**Security Requirement**

This learning data MUST be protected similarly to `ORGANIZATION → real organization mappings`.

**Critical Rule**

Learning data must NOT be stored inside the PDF itself, because the system needs **cross-document learning** across the entire Zone 1 collection.

**Suggested Storage**

```
data/zone1/entity_learning/
```

Possible formats: SQLite, JSONL, vector store (later)

---

### Learning Modes

#### 1. Manual Only (default — safest mode)

No LLM involvement. System learns only from manual user annotations.

#### 2. Local LLM Assisted

Uses LM Studio / Ollama / local OpenAI-compatible server. No confidential data leaves the machine.

#### 3. External LLM Assisted

Optional. Uses configured BYOK provider. Requires explicit Zone 1 user permission.

**Default Behaviour**

Default should be manual-first, with local-only assistance preferred.

---

## Rehydrate / Revert

**Requirement**

Users must always be able to **Rehydrate** and **Revert**.

### Rehydrate

Repopulate anonymized placeholders using mapping files stored in Zone 1.

### Revert

Undo recent anonymization changes.

### Mapping Storage

Mappings should live alongside the PDF source package, separated from export outputs.

---

## Export System

### Existing Reviewer Exports

The current exports remain:
- CSV
- TMX
- Markdown

These are human reviewer exports and remain accessible from the bottom export menu.

---

### New Export Type: Send to LLM

Add new button: **Send to LLM**

#### Send to LLM Flow

**Step 1 — Select Model**

Open modal similar to the PDF OCR stage, using BYOK provider selection.

**Step 2 — Select Prompt**

- **Option A:** Choose prompt from `data/prompts/`
- **Option B:** Enter custom prompt in textarea

The selected export file is automatically included in the prompt context.

**Output Behaviour**

LLM output saved to:
```
data/zone2/llm_reports/
```

#### Confidential Export Toggle

Add unchecked checkbox:
```
[ ] Include confidential information
```

Default: **OFF**

**Warning Prompt**

If enabled, show:

> Are you sure?
> This export may expose confidential information to the selected LLM provider.

#### Rehydration of LLM Results

After LLM processing, users must be able to press **Rehydrate** to restore confidential entities into the returned report using Zone 1 mapping files.

---

## Authentication

### Goal

Add lightweight access restriction for Zone 1. This is NOT enterprise auth yet.

### Pre-Upload Authentication Screen

Before entering Zone 1, require login.

### Initial Implementation

Use JSON-backed dummy auth.

**Example `data/users.json`:**

```json
[
  { "username": "zone1_admin", "password": "redzone123", "zones": ["zone1"] },
  { "username": "review_user", "password": "review123",  "zones": ["zone1"] },
  { "username": "test_user",   "password": "test123",    "zones": ["zone1"] }
]
```

### Session Reminder Popup

When inside the Red Zone, show a periodic reminder popup every N configurable minutes.

**Reminder Message:**

> You are currently operating in Zone 1 (Confidential Zone).
> Do not export confidential information unless explicitly required and approved.

**Configurable Setting:**

```
CONFIDOC_REDZONE_REMINDER_MINUTES=10
```

---

## Suggested Future Enhancements

1. Real authentication backend
2. Role-based permissions
3. Per-zone access rules
4. Audit logging
5. Export approval workflows
6. Cryptographically signed exports
7. Encrypted mapping storage
8. Entity confidence scoring
9. Local embedding-based entity memory
10. Secure offline-only mode

---

## Architectural Boundary

### BYOK Responsibilities
- provider abstraction
- vision requests
- text requests
- local/cloud model routing

### Confidoc Responsibilities
- entity learning
- PDF storage
- Zone security
- rehydration
- manual annotation
- export controls
- audit workflows

---

## Implementation Decisions (2026-05-16)

Answers to design questions, recorded for implementation.

---

### PDF Viewer — Decision

**Use option (a):** save rendered page PNGs to `data/zone1/previews/{job_id}/` during OCR.

Rationale:
- avoids adding PDF.js dependency now
- works equally well for scanned PDFs
- reuses the existing PyMuPDF render path
- keeps everything inside Zone 1
- good enough for review and annotation

Add PDF.js later only if text-layer fidelity, advanced zoom, or search is needed.

---

### Authentication — Decision

**Full-page login screen** gating the entire app including job list and sidebar.

- Sessions persist across browser refresh via a simple secure cookie/session
- Sessions are not indefinite — add a configurable timeout
- Add a **Logout** button inside the app
- Prototype only: users stored in a JSON file, three dummy Zone 1 users
- No enterprise auth yet

---

### Re-Detect / Entity Learning — Decision

Both deterministic and LLM-assisted, but **phased**:

**Phase 1 — Deterministic only (default)**

Manual annotations feed a secure Zone 1 learning store. The learning store influences deterministic detection:
- known person names
- known organizations
- known addresses / clinics / hospitals
- preferred entity type corrections
- false positives / ignore list

**Phase 2 — LLM-assisted (optional)**

The same learning store enriches the LLM prompt with local examples. Only active if a Zone 1 user explicitly enables LLM-assisted detection.

**Default must remain manual/deterministic first.**

---

### Send to LLM — Artifact Priority Decision

Default artifact is the **reviewed/pseudonymized markdown** — the safest approved export.

Priority order:
1. `reviewed_md` — if available (default)
2. pseudonymized / anonymized markdown
3. `normalized_md` — only if explicitly selected by Zone 1 user

Raw/original markdown must **not** be sent by default.

The `[ ] Include confidential information` checkbox controls whether rehydrated/confidential content is used instead.

---

### Starter Prompts — Decision

Pre-seed `data/prompts/` with:

| File | Purpose |
|---|---|
| `summarize_document.md` | Structured document summary |
| `translate_to_english_preserve_structure.md` | Translation preserving markdown structure |
| `extract_structured_medical_summary.md` | Extract diagnoses, dates, medications etc. |
| `reviewer_qa_check.md` | QA pass — check for anonymization gaps or inconsistencies |

**All prompts assume anonymized input by default.**

---

## Further Implementation Decisions (2026-05-16)

**Build order:** Items 1+2 first (PDF preview modal) — quick win, low risk, no auth entanglement. Auth (item 3) as a separate clean change set after.

**Learning store encryption:** Use JSONL or SQLite with `0o600` permissions for Phase 1. No Fernet encryption yet. Design the storage wrapper so encryption can be added later without changing the rest of the app.

**Session timeout:** 8 hours default.
```
CONFIDOC_SESSION_TIMEOUT_HOURS=8
```

**Reminder popup:** Separate from session timeout — periodic awareness only.
```
CONFIDOC_REDZONE_REMINDER_MINUTES=10
```

- Reminder = periodic awareness prompt
- Session timeout = actual login expiry

---

## Implementation Order

| # | Feature | Status | Notes |
|---|---|---|---|
| 1 | PNG preview save during OCR | **next** | Extend ingest.py — save PNGs to `data/zone1/previews/{job_id}/` |
| 2 | In-app PDF modal viewer | **next** | Serve via `/api/jobs/{job_id}/preview/{page}`, in-app modal |
| 3 | Login screen + session auth | pending | Full-page gate, JSON users, `CONFIDOC_SESSION_TIMEOUT_HOURS=8`, logout |
| 4 | Session reminder popup | pending | `CONFIDOC_REDZONE_REMINDER_MINUTES=10` |
| 5 | Zone 1 entity learning store | pending | JSONL/SQLite `0o600`, encryption-ready wrapper |
| 6 | Re-Detect uses learning store | pending | Deterministic pass augmented by learned names/orgs/ignore list |
| 7 | `data/prompts/` + starter prompts | pending | 4 pre-seeded prompts, anonymized-input assumption |
| 8 | Send to LLM flow | pending | Modal: provider → prompt → send; output to `data/zone2/llm_reports/` |
| 9 | Confidential export toggle | pending | Checkbox + warning; rehydrated vs pseudonymized artifact |
| 10 | Phase 2: LLM-assisted learning | pending | Learning store → few-shot LLM prompt enrichment (opt-in) |
