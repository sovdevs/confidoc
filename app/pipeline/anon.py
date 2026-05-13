"""Anonymization stage: detect PII entities in extracted markdown.

Current implementation is rule-based (regex). Each pattern produces
Entity objects that go into the job for human review. The reviewer
can approve, edit, or dismiss each one before the redacted file is written.

Extend this module with an LLM-assisted pass when ready — the HITL
checkpoint and job model are designed to absorb that without interface changes.
"""

import re
from pathlib import Path

from app.config import settings
from app.storage import audit_log, jobs as job_store
from app.storage.jobs import Entity, Job, JobStatus


# ── PII patterns ─────────────────────────────────────────────────────────────
# Each entry: (label, compiled_regex, replacement_template, use_group1)
#   use_group1=True  → m.group(1) is the PII span (prefix provides context but is not redacted)
#   use_group1=False → m.group() (full match) is the PII span
# Order matters — more specific / longer patterns first so subsumption can suppress subsets.

_PATTERNS: list[tuple[str, re.Pattern, str, bool]] = [
    # Medical / case record numbers
    ("CASE_ID",
     re.compile(r'\b(?:Case|Fallnummer|Aufnahme-?Nr\.?|Patientennummer)\s*[:\.]?\s*(\d{5,12})\b', re.I),
     "[CASE_ID]", True),

    # German / US date formats
    ("DATE",
     re.compile(r'\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b'),
     "[DATE]", False),

    # ISO dates
    ("DATE",
     re.compile(r'\b\d{4}-\d{2}-\d{2}\b'),
     "[DATE]", False),

    # Full German address: postal code + city + street (capture as one entity)
    ("ADDRESS",
     re.compile(
         r'\b\d{5}\s+[A-ZÄÖÜ][a-zA-ZäöüÄÖÜß-]+,\s*'
         r'[A-ZÄÖÜ][a-zäöüß]+(?:straße|weg|platz|allee|gasse|ring|damm|str\.)\s*\d+\b',
         re.I,
     ),
     "[ADDRESS]", False),

    # German street with full suffix (Hauptstraße 12, Lindenweg 5)
    ("ADDRESS",
     re.compile(r'\b[A-ZÄÖÜ][a-zäöüß]+-(?:straße|weg|platz|allee|gasse|ring|damm)\s+\d+\b', re.I),
     "[ADDRESS]", False),

    # German street with abbreviated suffix (Westfalenstr. 10, Dornhofstr. 34)
    ("ADDRESS",
     re.compile(r'\b[A-ZÄÖÜ][a-zäöüß]+str\.?\s*:?\s*\d+\b', re.I),
     "[ADDRESS]", False),

    # US-style street addresses
    ("ADDRESS",
     re.compile(r'\b\d+\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:St|Ave|Blvd|Rd|Ln|Dr|Ct|Way|APT)\b', re.I),
     "[ADDRESS]", False),

    # German zip + city (including hyphenated names like Neu-Isenburg; allow OCR lowercase after hyphen)
    ("LOCATION",
     re.compile(r'\b\d{5}\s+[A-ZÄÖÜ][a-zA-ZäöüÄÖÜß]+(?:-[A-Za-zäöüÄÖÜß]+)*\b'),
     "[LOCATION]", False),

    # US zip codes (standalone 5-digit or ZIP+4); subsumed if already covered by LOCATION
    ("LOCATION",
     re.compile(r'\b\d{5}(?:-\d{4})?\b'),
     "[LOCATION]", False),

    # Patient / person names preceded by known labels.
    # No re.I — requires properly capitalised names so common lowercase words don't match.
    ("PATIENT_NAME",
     re.compile(
         r'(?:Patien(?:t|tin)|Name|Vorname|Nachname|geb\.?\s*(?:am\s*)?)\s*[,:\.]?\s*'
         r'([A-ZÄÖÜ][a-zäöüß]+(?:[\s-][A-ZÄÖÜ][a-zäöüß]+){0,3})',
     ),
     "[PATIENT_NAME]", True),

    # Physician names after German letter closing ("Mit freundlichen Grüßen\n\nZamperoni")
    # Tolerates OCR variants: missing space after period, alternate spellings of Grüßen
    ("PHYSICIAN_NAME",
     re.compile(
         r'(?:Mit\.?\s*freundlichen\s+\w+|MfG)\s*\n+\s*'
         r'([A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+){0,3})',
     ),
     "[PHYSICIAN_NAME]", True),

    # Social security / insurance numbers (rough heuristic)
    ("ID_NUMBER",
     re.compile(r'\b[A-Z]\d{9,12}\b'),
     "[ID_NUMBER]", False),

    # Phone numbers
    ("PHONE",
     re.compile(r'\b(?:\+\d{1,3}\s?)?(?:\(?\d{2,5}\)?[\s\-/])?\d{3,6}[\s\-/]\d{3,8}\b'),
     "[PHONE]", False),
]


def detect(text: str) -> list[Entity]:
    """Return all PII entities found in text, ordered by position."""
    entities: list[Entity] = []
    seen: set[tuple[int, int]] = set()

    for label, pattern, replacement, use_group in _PATTERNS:
        for m in pattern.finditer(text):
            if use_group and m.lastindex:
                start, end, pii_text = m.start(1), m.end(1), m.group(1)
            else:
                start, end, pii_text = m.start(), m.end(), m.group()

            span = (start, end)
            if span in seen:
                continue
            # Skip spans fully subsumed by an already-accepted entity
            if any(s <= start and end <= e for s, e in seen):
                continue
            seen.add(span)
            entities.append(Entity(
                label=label,
                text=pii_text,
                start=start,
                end=end,
                replacement=replacement,
            ))

    return sorted(entities, key=lambda e: e.start)


def apply(text: str, entities: list[Entity]) -> str:
    """Apply approved replacements to text (right-to-left to preserve offsets)."""
    approved = sorted(
        [e for e in entities if e.approved],
        key=lambda e: e.start,
        reverse=True,
    )
    for e in approved:
        text = text[: e.start] + e.replacement + text[e.end :]
    return text


# ── Pipeline step ─────────────────────────────────────────────────────────────

def run(job: Job) -> Job:
    """Detect PII in extracted markdown and attach entities to the job."""
    if not job.extracted_md:
        raise RuntimeError("No extracted markdown path on job")

    md_path = settings.jobs_dir.parent / job.extracted_md
    text = md_path.read_text(encoding="utf-8")

    entities = detect(text)
    job_store.update_status(job.id, JobStatus.reviewing, entities=[e.model_dump() for e in entities])
    audit_log.log(job.id, "anon_detected", {"entity_count": len(entities)})

    return job_store.load(job.id)
