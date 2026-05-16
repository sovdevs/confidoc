# Confidoc Prepared Package

| Field | Value |
|---|---|
| Package ID | `a4294f67426c` |
| Job ID | `5b992fd488e64c3ca5b75996dd2e7160` |
| Task | clinical_summary |
| Profile | clinical_summary |
| Strictness applied | **strict** |
| Prepared at | 2026-05-16 13:01 UTC |
| Recommendation | ⚠ **review_required** |

---

## Privacy Actions Applied

| Action | Count | Meaning |
|--------|-------|---------|
| `relative_date` | 8 | Date replaced with relative chronology marker |
| `remove` | 3 | Removed from prepared document |
| `generalize` | 2 | Location generalized to region/country |
| `flag_for_review` | 2 | Preserved with reidentification risk flag |
| `stable_token` | 1 | Replaced with numbered stable placeholder |

---

## Usefulness Assessment: ✓ PASS (1.00 / 0.90 threshold)

| Check | Weight | Result |
|-------|--------|--------|
| Sufficient text remains for the task | — | ✓ |
| All tokens follow stable numbered format | — | ✓ |
| Medical diagnoses remain in document | 25% | ✓ |
| Clinical measurements (values + units) remain | 20% | ✓ |
| Date or time markers remain for chronology | 20% | ✓ |
| Treatment plan / follow-up context remains | 15% | ✓ |
| Medication names remain in document | 10% | ✓ |
| Document headings/sections largely intact | 10% | ✓ |

---

## Risk Assessment: ⚠ MEDIUM

| Category | Level |
|----------|-------|
| Direct identifiers | medium |
| Quasi-identifiers | low |

**Risk warnings:**

- Full names may still be present in prepared document

---

---

---

## Package Contents

| File | Purpose |
|------|---------|
| `prepared.md` | Anonymized/pseudonymized document for Zone 2 |
| `manifest.json` | Package summary, counts, decision, checksums |
| `risk_report.json` | Re-identification risk assessment |
| `usefulness_report.json` | Task usefulness assessment |
| `transformation_log.json` | Entity transformation audit trail (no PHI) |
| `preview.md` | This human-readable summary |

> **Zone 1 note:** The token-to-original mapping is stored encrypted in Zone 1 only.
> It is **not** included in this package.