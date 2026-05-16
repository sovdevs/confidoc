"""LLM-assisted PII detection — privacy-safe design.

Privacy model:
  1. The regex pass runs first and catches structural PII (dates, postcodes, phones, …).
  2. Those spans are MASKED before anything is sent to the LLM, so already-identified
     PII never leaves the system.
  3. The LLM receives only the partially-redacted text.  The unmasked portions are what
     the regex could not catch — that is exactly what we need the LLM to find.
  4. Few-shot guidance uses label descriptions and per-type counts from
     approved_terms.jsonl — no actual PII values are transmitted.
  5. The LLM returns verbatim strings from the masked text; we locate those strings in
     the original text to recover exact character positions.

Provider: configured via CONFIDOC_ANON_PROVIDER / CONFIDOC_ANON_MODEL (see config.py).
Default: OpenRouter → google/gemini-2.0-flash.
"""

import json
import logging
from typing import Any

from app.config import settings
from app.services import llm_adapter
from app.storage import audit_log, jobs as job_store
from app.storage.jobs import Entity, Job

logger = logging.getLogger(__name__)

# ── Label descriptions — few-shot guidance without raw PII values ─────────────
_LABEL_DESCRIPTIONS: dict[str, str] = {
    "PATIENT_NAME":   "patient full names (first and/or last name)",
    "PHYSICIAN_NAME": "doctor or physician surnames, often after a letter closing such as 'Mit freundlichen Grüßen'",
    "DATE":           "dates in any format (DD.MM.YYYY, YYYY-MM-DD, written out, …)",
    "ADDRESS":        "street addresses including street name and building number",
    "LOCATION":       "postal/zip codes combined with city names",
    "CASE_ID":        "case reference or patient record numbers",
    "ID_NUMBER":      "insurance policy or national ID numbers",
    "PHONE":          "telephone or fax numbers",
}

_SYSTEM_PROMPT = """\
You are a PII (personally identifiable information) detection assistant for medical and \
clinical documents, primarily in German with some English.

The document you will receive has already been PARTIALLY REDACTED: spans caught by a \
rule-based system have been replaced with tokens such as [PATIENT_NAME], [DATE], \
[ADDRESS], etc.  Your job is to find any PII that was MISSED — text still appearing in \
plain form that should have been redacted.

Return your findings as a JSON array.  Each element must have exactly two keys:
  "label" — one of: PATIENT_NAME, PHYSICIAN_NAME, DATE, ADDRESS, LOCATION,
                     CASE_ID, ID_NUMBER, PHONE
  "text"  — the EXACT verbatim substring as it still appears in the partially-redacted
             document.  Copy character-for-character, including OCR artefacts.

Rules:
- Return ONLY a valid JSON array. No prose, no markdown fences, no explanation.
- Do NOT report tokens that are already redacted (do not return "[DATE]" or "[ADDRESS]").
- List EVERY occurrence individually.
- Do NOT include generic clinical terms ("Patientin" alone is not a name).
- If nothing was missed, return [].
"""


def _build_guidance(counts: dict[str, int]) -> str:
    if not counts:
        return ""
    lines = ["From reviewing previous documents, human reviewers have confirmed:\n"]
    for label, count in sorted(counts.items()):
        desc = _LABEL_DESCRIPTIONS.get(label, label.lower().replace("_", " "))
        lines.append(f"  {label} ({count} confirmed so far): {desc}")
    return "\n".join(lines) + "\n"


def _label_counts() -> dict[str, int]:
    if not settings.approved_terms.exists():
        return {}
    counts: dict[str, set[str]] = {}
    for line in settings.approved_terms.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
            counts.setdefault(entry["label"], set()).add(entry["text"])
        except Exception:
            pass
    return {label: len(texts) for label, texts in counts.items()}


def _mask_text(text: str, entities: list[Entity]) -> str:
    for e in sorted(entities, key=lambda e: e.start, reverse=True):
        text = text[: e.start] + e.replacement + text[e.end :]
    return text


def _find_all(text: str, substring: str) -> list[tuple[int, int]]:
    positions, start = [], 0
    while (idx := text.find(substring, start)) != -1:
        positions.append((idx, idx + len(substring)))
        start = idx + 1
    return positions


async def run(job: Job) -> Job:
    """Detect missed PII via LLM on the partially-masked document."""
    if not job.extracted_md:
        return job
    if not settings.anon_api_key:
        logger.warning("No anon API key configured — skipping LLM PII pass")
        return job

    md_path = settings.jobs_dir.parent / job.extracted_md
    original_text = md_path.read_text(encoding="utf-8")

    existing = [Entity.model_validate(e) for e in job.entities]
    taken: set[tuple[int, int]] = {(e.start, e.end) for e in existing}

    # Mask already-detected spans — only genuinely novel PII reaches the LLM
    masked_text = _mask_text(original_text, existing)

    guidance = _build_guidance(_label_counts())
    user_prompt = (
        f"{guidance}\n"
        "Find any PII still visible in the partially-redacted document below.\n"
        "Return a JSON array of {\"label\", \"text\"} objects.\n\n"
        "--- DOCUMENT (PARTIALLY REDACTED) START ---\n"
        f"{masked_text}\n"
        "--- DOCUMENT END ---"
    )

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user",   "content": user_prompt},
    ]

    try:
        content = await llm_adapter.anon_complete(
            messages=messages,
            response_format={"type": "json_object"},
        )
        findings: list[dict[str, Any]] = json.loads(content.strip())
    except Exception as exc:
        logger.warning("LLM PII pass failed for job %s: %s", job.id, exc)
        audit_log.log(job.id, "llm_anon_failed", {"error": str(exc)})
        return job

    # Locate found strings in the ORIGINAL (unmasked) text to recover offsets
    new_entities: list[Entity] = []
    for item in findings:
        label    = (item.get("label") or "").strip()
        pii_text = (item.get("text")  or "").strip()
        if not label or not pii_text or pii_text.startswith("["):
            continue
        for start, end in _find_all(original_text, pii_text):
            span = (start, end)
            if span in taken:
                continue
            if any(s <= start and end <= e for s, e in taken):
                continue
            taken.add(span)
            new_entities.append(Entity(
                label=label, text=pii_text,
                start=start, end=end,
                replacement=f"[{label}]",
            ))

    all_entities = sorted(existing + new_entities, key=lambda e: e.start)
    job_store.update_status(job.id, job.status, entities=[e.model_dump() for e in all_entities])
    audit_log.log(job.id, "llm_anon_done", {
        "provider": settings.anon_provider,
        "model": settings.anon_model,
        "llm_new": len(new_entities),
        "regex_kept": len(existing),
        "total": len(all_entities),
    })
    return job_store.load(job.id)
