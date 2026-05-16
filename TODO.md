# Confidoc — Questions / TODO List

---

## 1. Canonical Mapping & Export Reindexing

### Questions

- Is there currently always exactly one canonical token mapping state per document/package?
- Are placeholders regenerated fresh on every export?
- If entities are deleted during editing, can gaps currently appear (DATE_001, DATE_003)?
- Is placeholder numbering tied to insertion order, UUID order, or UI order?
- Is export deterministic across repeated exports of the same unchanged document?

### TODO

- Refactor placeholder generation to occur only at export/finalization stage
- Internally track entities using immutable UUIDs instead of placeholder IDs
- Add export-time canonical renumbering:
  - sequential numbering
  - no gaps
  - deterministic ordering
- Ensure deleted entities cannot leave numbering artifacts
- Add tests:
  - deletion/reinsertion
  - reorder stability
  - repeated export stability

---

## 2. Mapping Encryption & Isolation

### Questions

- Is token_map.enc currently encrypted with:
  - per-document key?
  - app-wide key?
  - user/org key?
- Where are encryption keys stored?
- Can one document's mappings ever be resolved from another package?
- Are old mapping files/version histories retained anywhere?
- Are temp plaintext mapping files ever written to disk?

### TODO

- Verify document-local mapping isolation
- Ensure no plaintext mapping cache survives processing
- Confirm secure deletion policy for temporary files
- Add architecture note:
  - mappings are document-local only
  - placeholders have no global meaning

---

## Architecture Boundary (implemented 2026-05-16)

| Store | Purpose | Contains PHI? | Reversible? |
|---|---|---|---|
| `data/mappings/{job_id}.enc` | Token ↔ original value | **Yes** | **Yes** — decrypt with MAPPING_KEY |
| `data/approved_terms.jsonl` | Structural learning signals | **No** | No — shapes and buckets only |

**Rule:** No raw entity value, filename, or job ID may enter `approved_terms.jsonl`.
The encrypted mapping is the sole store of original values and is document-local.

---

## 3. Learning Store / Auto-Detection Memory

### Questions

- What exactly is currently persisted to improve future auto-detection?
- Are raw entity values ever stored globally?
- Does learning currently use:
  - regex patterns?
  - contextual phrases?
  - embeddings?
  - accepted/rejected user actions?
- Is there any possibility PHI/PII leaks into:
  - logs
  - telemetry
  - embeddings
  - vector DBs
  - analytics

### TODO

- Separate:
  - reversible document mappings
  - non-reversible learning signals
- Create explicit learning_store.jsonl schema
- Restrict learning data to:
  - structural patterns
  - token shapes
  - confidence adjustments
  - context windows
  - acceptance/rejection metrics
- Explicitly prohibit storing:
  - names
  - addresses
  - phones
  - IDs
  - bank info
- Add privacy audit logging

---

## 4. Entity Styling / Semantic Color System

### Questions

- Why do some entity types lack background colors?
- Which entity types currently have incomplete CSS classes?
- Is styling generated dynamically or hardcoded?
- Is there a central entity-type theme map?

### TODO

- Create unified semantic color system
- Add full styling for:
  - ORGANIZATION
  - OTHER_PII
  - BANK_INFORMATION
  - ID_NUMBER
  - LOCATION
  - PHYSICIAN_NAME
  - EMAIL
  - INSURANCE_ID
  - DIAGNOSIS
  - MEDICATION
- Standardize: background, border, hover, text color, selected state
- Add accessibility contrast checks
- Ensure dark-mode consistency

---

## 5. Placeholder Namespace Design

### Questions

- Are placeholders globally unique?
- Could placeholders collide across documents?
- Are placeholder prefixes configurable?
- Are placeholders stable across edits?

### TODO

- Make placeholders document-scoped only
- Ensure internal IDs use UUIDs
- Ensure export placeholders are presentation-only
- Add placeholder namespace abstraction layer

---

## 6. Security / Metadata Leakage

### Questions

- Could numbering gaps leak edit history?
- Could placeholder order leak insertion chronology?
- Are deleted mappings recoverable from logs/history?
- Are audit logs storing sensitive originals?

### TODO

- Eliminate numbering gaps
- Remove edit-history leakage from exported mappings
- Audit all logs for accidental raw-value persistence
- Add secure redaction pass before logging

---

## 7. Export Pipeline

### Questions

- At what exact stage does de-anonymization occur?
- Is de-anonymization streaming or full-buffer?
- Can exports occur without de-anonymization?
- Are mappings validated before export?

### TODO

- Add explicit export stages:
  1. validate mappings
  2. canonicalize IDs
  3. rebuild placeholder map
  4. de-anonymize
  5. export final artifact
- Add export integrity verification
- Add orphan-token detection

---

## 8. Architecture Documentation

### TODO

Create markdown docs:

**`docs/security/token-mapping.md`**
- document-local mapping model
- encryption approach
- export canonicalization
- de-anonymization lifecycle

**`docs/security/learning-store.md`**
- what is learned
- what is NEVER learned
- PHI exclusion guarantees
- feature-only learning architecture

**`docs/ui/entity-color-system.md`**
- semantic palette
- accessibility rules
- entity styling conventions

---

## 9. Nice-to-Have Improvements

- Add "Rebuild Canonical IDs" debug button
- Add mapping consistency validator
- Add encrypted mapping integrity checksum
- Add export reproducibility mode
- Add "show learning signals" developer view
- Add document-level cryptographic package fingerprint

---

## 10. Priority Order

**Priority 1**
- Export-time canonical reindexing
- UUID-based internal entity IDs
- Learning store separation from encrypted mappings

**Priority 2**
- Full semantic color system
- Security audit of logs/temp files

**Priority 3**
- Formal architecture docs
- Integrity validation tooling
