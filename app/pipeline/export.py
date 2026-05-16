"""Export stage: tokenize, redact, write TMX/CSV, save encrypted mapping."""

import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pdf_to_markdown.exporter import md_to_segments, write_tmx

from app.config import settings
from app.storage import audit_log, jobs as job_store
from app.storage import mappings as mapping_store
from app.storage.jobs import Entity, Job, JobStatus
from app.pipeline import anon


# ── PHI-free learning signal helpers ─────────────────────────────────────────

def _text_shape(text: str) -> str:
    """Return structural character-class pattern — no original chars retained.

    Examples:  "Maria Schmidt" → "Aa+ Aa+"
               "01.01.1980"   → "00.00.0000"
               "Musterstr. 1" → "Aa+Aa+. 0"
    """
    s = re.sub(r'[A-ZÜÄÖÉÀÈÊÂÎÔÙÛÇÑ]', 'A', text)
    s = re.sub(r'[a-züäöéàèêâîôùûçñß]', 'a', s)
    s = re.sub(r'\d', '0', s)
    s = re.sub(r'A{2,}', 'A+', s)
    s = re.sub(r'a{2,}', 'a+', s)
    s = re.sub(r'0{2,}', '0+', s)
    return s


def _len_bucket(n: int) -> str:
    if n <= 4:  return "1-4"
    if n <= 7:  return "5-7"
    if n <= 12: return "8-12"
    if n <= 20: return "13-20"
    return "21+"


def _h(s: str, length: int = 12) -> str:
    """Non-reversible SHA-256 prefix for correlating signals within a session."""
    return hashlib.sha256(s.encode()).hexdigest()[:length]


def _log_approved_terms(job: Job, entities: list[Entity]) -> None:
    """Append PHI-free structural learning signals to the learning store.

    PRIVACY GUARANTEE: no raw entity text, no filenames, no reversible values
    are written. Only character-class shapes and bucketed lengths.
    The encrypted per-document mapping is the sole store of original values.
    """
    ts = datetime.now(timezone.utc).isoformat()
    job_hash  = _h(job.id)
    file_hash = _h(job.filename, 8)

    with settings.approved_terms.open("a", encoding="utf-8") as f:
        for e in entities:
            if not e.approved:
                continue
            t = e.text
            f.write(json.dumps({
                "ts":               ts,
                "job_id_hash":      job_hash,
                "filename_hash":    file_hash,
                "label":            e.label,
                "manual":           e.manual,
                "source":           "manual_selection" if e.manual else "auto_detected",
                "text_shape":       _text_shape(t),
                "char_len_bucket":  _len_bucket(len(t)),
                "word_count":       len(t.split()),
                "has_digits":       bool(re.search(r'\d', t)),
                "has_letters":      bool(re.search(r'[A-Za-züäöüÄÖÜß]', t)),
                "has_punctuation":  bool(re.search(r'[^\w\s]', t)),
                "accepted":         True,
            }, ensure_ascii=False) + "\n")


def run(job: Job, reviewer: str = "human",
        export_tmx: bool = True, export_csv: bool = True) -> Job:
    """Tokenize approved entities, write redacted outputs, save encrypted mapping.

    Source markdown priority:
      1. normalized_md  — if OCRCheck has been run and approved
      2. reviewed_md    — pseudonymized but not yet normalized (normalization skipped)
      3. extracted_md   — fallback for jobs imported at an earlier stage
    """
    # Prefer the furthest-along artifact that exists
    source_md: Optional[str] = None
    for candidate in (job.normalized_md, job.reviewed_md, job.extracted_md):
        if candidate:
            p = settings.jobs_dir.parent / candidate
            if p.exists():
                source_md = candidate
                break
    if not source_md:
        raise RuntimeError("No markdown source found on job (normalized, reviewed, or extracted)")

    md_path = settings.jobs_dir.parent / source_md
    text = md_path.read_text(encoding="utf-8")
    entities = [Entity.model_validate(e) for e in job.entities]

    # ── 1. Log approved terms BEFORE tokenization (uses generic labels) ───────
    _log_approved_terms(job, entities)

    # ── 2. Assign stable numbered tokens ─────────────────────────────────────
    tokenized_entities, token_map = mapping_store.assign_tokens(entities)

    # ── 3. Apply tokenized replacements to produce reviewed markdown ──────────
    redacted = anon.apply(text, tokenized_entities)

    stem = Path(job.filename).stem
    reviewed_path = settings.reviewed_dir / f"{stem}_reviewed.md"
    reviewed_path.write_text(redacted, encoding="utf-8")
    rel_reviewed = str(reviewed_path.relative_to(settings.jobs_dir.parent))

    approved_count = sum(1 for e in entities if e.approved)
    manual_count   = sum(1 for e in entities if e.approved and e.manual)
    audit_log.log(job.id, "review_applied", {
        "approved": approved_count,
        "manual": manual_count,
        "tokens_assigned": len(token_map),
        "reviewer": reviewer,
    })

    # ── 4. Save encrypted mapping ─────────────────────────────────────────────
    map_path = mapping_store.save(job.id, token_map, created_by=reviewer)
    rel_map  = str(map_path.relative_to(settings.jobs_dir.parent))
    audit_log.log(job.id, "MAPPING_CREATED", {
        "path": rel_map,
        "token_count": len(token_map),
    })

    # ── 5. Export TMX / CSV from tokenized (pseudonymized) markdown ───────────
    job_store.update_status(job.id, JobStatus.exporting,
                            reviewed_md=rel_reviewed, mapping_path=rel_map)
    segments = md_to_segments(redacted)

    tmx_path = settings.exported_dir / f"{stem}.tmx"
    csv_path = settings.exported_dir / f"{stem}.csv"

    if export_tmx:
        write_tmx(segments, job.src_lang, job.tgt_lang, tmx_path)

    if export_csv:
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL)
            writer.writerow(["id", job.src_lang])
            for i, seg in enumerate(segments, 1):
                writer.writerow([i, seg])

    rel_tmx = str(tmx_path.relative_to(settings.jobs_dir.parent))
    rel_csv = str(csv_path.relative_to(settings.jobs_dir.parent))

    # Update entities in job JSON with stable tokens so the UI reflects them
    job_store.update_status(
        job.id, JobStatus.done,
        exported_tmx=rel_tmx,
        exported_csv=rel_csv,
        reviewed_by=reviewer,
        entities=[e.model_dump() for e in tokenized_entities],
    )
    audit_log.log(job.id, "EXPORT_GENERATED", {
        "tmx": rel_tmx,
        "csv": rel_csv,
        "segments": len(segments),
        "terms_logged": approved_count,
        "manual_terms": manual_count,
    })

    return job_store.load(job.id)
