"""Export stage: tokenize, redact, write TMX/CSV, save encrypted mapping."""

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pdf_to_markdown.exporter import md_to_segments, write_tmx

from app.config import settings
from app.storage import audit_log, jobs as job_store
from app.storage import mappings as mapping_store
from app.storage.jobs import Entity, Job, JobStatus
from app.pipeline import anon


def _log_approved_terms(job: Job, entities: list[Entity]) -> None:
    """Append approved entities to the LLM few-shot knowledge base.

    Logged BEFORE tokenization so the generic label (e.g. [PATIENT_NAME])
    is recorded, not the numbered token ([PATIENT_NAME_001]).
    """
    ts = datetime.now(timezone.utc).isoformat()
    with settings.approved_terms.open("a", encoding="utf-8") as f:
        for e in entities:
            if not e.approved:
                continue
            f.write(json.dumps({
                "ts": ts,
                "job_id": job.id,
                "filename": job.filename,
                "label": e.label,
                "text": e.text,
                "replacement": f"[{e.label}]",   # generic label, never the numbered token
                "manual": e.manual,
            }, ensure_ascii=False) + "\n")


def run(job: Job, reviewer: str = "human") -> Job:
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

    # TMX: bilingual, requires explicit target language set by Zone 1 user
    write_tmx(segments, job.src_lang, job.tgt_lang, tmx_path)

    # CSV: source-only (id + source segments) — no target column
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
