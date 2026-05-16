"""Review UI routes — FastAPI router for the HITL anonymization interface."""

import asyncio
import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.config import settings
from app.pipeline import anon, anon_llm, export, ingest, ocr_check
from app.storage import mappings as mapping_store
from app.storage import audit_log
from app.storage import jobs as job_store
from app.storage.jobs import Entity, Job, JobStatus

_STABLE_TOKEN_RE = re.compile(r'\[[A-Z][A-Z_]*_\d{3}\]')


class NormalizeBody(BaseModel):
    markdown: str

router = APIRouter()

_INDEX_HTML = Path(__file__).parent / "templates" / "index.html"


@router.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(_INDEX_HTML.read_text(encoding="utf-8"))


# ── Jobs ────────────────────────────────────────────────────────────────────

@router.get("/api/jobs")
def list_jobs():
    return [j.model_dump() for j in job_store.list_all()]


@router.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = job_store.load(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job.model_dump()


@router.get("/api/jobs/{job_id}/audit")
def get_audit(job_id: str):
    return audit_log.read(job_id)


# ── Input directory listing ──────────────────────────────────────────────────

@router.get("/api/inputs")
def list_inputs():
    """Return all PDFs present in data/input/."""
    pdfs = sorted(p.name for p in settings.input_dir.glob("*.pdf"))
    return {"files": pdfs}


def _check_byok(api_key: str) -> None:
    """Raise 403 if BYOK-only mode is active and no user key was supplied."""
    if settings.byok_only and not api_key.strip():
        raise HTTPException(
            403,
            "This deployment requires you to supply your own API key. "
            "Enter it in the OCR Model step before processing."
        )


@router.post("/api/inputs/{filename}/process")
async def process_input(
    background_tasks: BackgroundTasks,
    filename: str,
    src_lang: str = Form("de-DE"),
    tgt_lang: str = Form("en-GB"),
    pdf_provider: str = Form(""),
    pdf_model: str = Form(""),
    pdf_api_key: str = Form(""),
):
    """Start a pipeline job for a PDF already in data/input/."""
    _check_byok(pdf_api_key)
    pdf_path = settings.input_dir / filename
    if not pdf_path.exists():
        raise HTTPException(404, f"{filename} not found in input directory")
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(400, "PDF files only.")

    pdf_bytes = pdf_path.read_bytes()
    job = Job(filename=filename, src_lang=src_lang, tgt_lang=tgt_lang)
    job_store.save(job)
    audit_log.log(job.id, "job_created", {"filename": filename, "source": "input_dir"})

    background_tasks.add_task(
        _run_ingest_and_detect, job, pdf_bytes,
        pdf_provider or None, pdf_model or None, pdf_api_key or None,
    )
    return {"job_id": job.id, "status": job.status}


# ── Upload & ingest ──────────────────────────────────────────────────────────

@router.post("/api/upload")
async def upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    src_lang: str = Form("de-DE"),
    tgt_lang: str = Form("en-GB"),
    pdf_provider: str = Form(""),
    pdf_model: str = Form(""),
    pdf_api_key: str = Form(""),
):
    _check_byok(pdf_api_key)
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "PDF files only.")

    pdf_bytes = await file.read()
    job = Job(filename=file.filename, src_lang=src_lang, tgt_lang=tgt_lang)
    job_store.save(job)
    audit_log.log(job.id, "job_created", {"filename": file.filename})

    background_tasks.add_task(
        _run_ingest_and_detect, job, pdf_bytes,
        pdf_provider or None, pdf_model or None, pdf_api_key or None,
    )
    return {"job_id": job.id, "status": job.status}


async def _run_ingest_and_detect(
    job: Job,
    pdf_bytes: bytes,
    override_provider: str | None = None,
    override_model: str | None = None,
    override_api_key: str | None = None,
) -> None:
    try:
        job = await ingest.run(job, pdf_bytes,
                               override_provider=override_provider,
                               override_model=override_model,
                               override_api_key=override_api_key)
        job = anon.run(job)
        job = await anon_llm.run(job)
    except Exception as e:
        job_store.update_status(job.id, JobStatus.failed, error=str(e))


# ── Re-detect (re-run anonymization, preserving manual entities) ─────────────

@router.post("/api/jobs/{job_id}/redetect")
async def redetect(job_id: str):
    job = _require_job(job_id)
    if not job.extracted_md:
        raise HTTPException(400, "Job has no extracted markdown — run ingest first")

    # Stash manually-added entities before auto-detection overwrites them
    manual_entities = [
        Entity.model_validate(e) for e in job.entities if e.get("manual")
    ]

    try:
        job = anon.run(job)                # regex pass
        job = await anon_llm.run(job)      # LLM pass with approved-terms few-shots
    except Exception as e:
        raise HTTPException(500, str(e))

    # Merge manual entities back — skip any whose span overlaps an auto-detected one
    auto = [Entity.model_validate(e) for e in job.entities]
    auto_spans = [(e.start, e.end) for e in auto]
    merged = list(auto)
    for m in manual_entities:
        overlaps = any(s < m.end and m.start < e for s, e in auto_spans)
        if not overlaps:
            merged.append(m)
    merged.sort(key=lambda e: e.start)

    job_store.update_status(job_id, job.status, entities=[e.model_dump() for e in merged])
    job = job_store.load(job_id)

    audit_log.log(job_id, "redetect", {
        "auto": len(auto),
        "manual_preserved": len(manual_entities),
        "total": len(merged),
    })
    return job.model_dump()


# ── Manual entity addition ────────────────────────────────────────────────────

@router.post("/api/jobs/{job_id}/entities")
def add_entity(
    job_id: str,
    label: str = Form(...),
    text: str = Form(...),
    start: int = Form(...),
    end: int = Form(...),
    replacement: str = Form(...),
):
    job = _require_job(job_id)
    new_entity = Entity(
        label=label, text=text, start=start, end=end,
        replacement=replacement, approved=True, manual=True,
    )
    entities = [Entity.model_validate(e) for e in job.entities]
    entities.append(new_entity)
    entities.sort(key=lambda e: e.start)
    job_store.update_status(job_id, job.status, entities=[e.model_dump() for e in entities])
    audit_log.log(job_id, "entity_added_manual", {
        "entity_id": new_entity.id, "label": label, "text": text,
    })
    return job_store.load(job_id).model_dump()


# ── Review actions ───────────────────────────────────────────────────────────

@router.post("/api/jobs/{job_id}/entities/{entity_id}/approve")
def approve_entity(job_id: str, entity_id: str):
    job = _require_job(job_id)
    job = _update_entity(job, entity_id, approved=True)
    audit_log.log(job_id, "entity_approved", {"entity_id": entity_id})
    return {"ok": True}


@router.post("/api/jobs/{job_id}/entities/{entity_id}/dismiss")
def dismiss_entity(job_id: str, entity_id: str):
    job = _require_job(job_id)
    job = _update_entity(job, entity_id, approved=False, dismissed=True)
    audit_log.log(job_id, "entity_dismissed", {"entity_id": entity_id})
    return {"ok": True}


@router.delete("/api/jobs/{job_id}/entities/{entity_id}")
def delete_entity(job_id: str, entity_id: str):
    """Permanently remove an entity so the text span can be re-annotated."""
    job = _require_job(job_id)
    before = len(job.entities)
    job.entities = [e for e in job.entities if e.id != entity_id]
    if len(job.entities) == before:
        raise HTTPException(404, f"Entity {entity_id} not found")
    job_store.save(job)
    audit_log.log(job_id, "entity_deleted", {"entity_id": entity_id})
    return {"ok": True}


@router.post("/api/jobs/{job_id}/entities/{entity_id}/edit")
def edit_entity(job_id: str, entity_id: str, replacement: Annotated[str, Form()]):
    job = _require_job(job_id)
    job = _update_entity(job, entity_id, approved=True, replacement=replacement, edited=True)
    audit_log.log(job_id, "entity_edited", {"entity_id": entity_id, "replacement": replacement})
    return {"ok": True}


@router.post("/api/jobs/{job_id}/approve-all")
def approve_all(job_id: str):
    job = _require_job(job_id)
    updated = []
    for e in job.entities:
        entity = Entity.model_validate(e)
        entity.approved = True
        updated.append(entity.model_dump())
    job_store.update_status(job_id, JobStatus.approved, entities=updated)
    audit_log.log(job_id, "all_entities_approved")
    return {"ok": True, "count": len(updated)}


# ── Export ───────────────────────────────────────────────────────────────────

# ── OCRCheck / Normalization ──────────────────────────────────────────────────

@router.post("/api/jobs/{job_id}/normalize/start")
def start_normalization(job_id: str):
    """Enter the normalization stage: copy reviewed_md as the working draft,
    run the first OCR pass, and set status → normalizing."""
    job = _require_job(job_id)
    if job.status not in (JobStatus.approved, JobStatus.normalizing, JobStatus.normalized, JobStatus.done):
        raise HTTPException(400, f"Cannot normalize from status '{job.status}'")
    if not job.reviewed_md:
        raise HTTPException(400, "No pseudonymized markdown found — run export after review first")

    reviewed_path = settings.jobs_dir.parent / job.reviewed_md
    if not reviewed_path.exists():
        raise HTTPException(404, "Reviewed markdown file not found on disk")

    text = reviewed_path.read_text(encoding="utf-8")

    # Initialize normalized draft as a copy of reviewed_md only if not already started
    stem = Path(job.filename).stem
    norm_path = settings.normalized_dir / f"{stem}_normalized.md"
    if not norm_path.exists():
        norm_path.write_text(text, encoding="utf-8")
    else:
        # Use the existing draft (reviewer may have already edited it)
        text = norm_path.read_text(encoding="utf-8")

    rel = str(norm_path.relative_to(settings.jobs_dir.parent))
    job_store.update_status(job_id, JobStatus.normalizing, normalized_md=rel)
    audit_log.log(job_id, "OCRCHECK_STARTED", {"source": job.reviewed_md})

    flags = ocr_check.detect(text)
    return {
        "job": job_store.load(job_id).model_dump(),
        "markdown": text,
        "flags": [f.as_dict() for f in flags],
    }


@router.get("/api/jobs/{job_id}/normalize")
def get_normalized_md(job_id: str):
    """Return current normalized markdown and OCR flags."""
    job = _require_job(job_id)
    source = job.normalized_md or job.reviewed_md
    if not source:
        raise HTTPException(404, "No normalized or reviewed markdown available")
    path = settings.jobs_dir.parent / source
    if not path.exists():
        raise HTTPException(404, "Markdown file not found on disk")
    text = path.read_text(encoding="utf-8")
    flags = ocr_check.detect(text)
    return {"markdown": text, "flags": [f.as_dict() for f in flags]}


@router.put("/api/jobs/{job_id}/normalize")
def save_normalized_md(job_id: str, body: NormalizeBody):
    """Save an edited markdown draft. Validates that all stable tokens survive unchanged."""
    job = _require_job(job_id)
    if job.status not in (JobStatus.normalizing, JobStatus.normalized, JobStatus.done):
        raise HTTPException(400, f"Job is not in normalization stage (status: {job.status})")

    # Token integrity check
    source = job.reviewed_md
    if source:
        original_text = (settings.jobs_dir.parent / source).read_text(encoding="utf-8")
        errors = ocr_check.validate_tokens(original_text, body.markdown)
        if errors:
            raise HTTPException(422, f"Token mismatch — stable tokens must not be modified: {errors}")

    stem = Path(job.filename).stem
    norm_path = settings.normalized_dir / f"{stem}_normalized.md"
    norm_path.write_text(body.markdown, encoding="utf-8")
    rel = str(norm_path.relative_to(settings.jobs_dir.parent))
    job_store.update_status(job_id, JobStatus.normalizing, normalized_md=rel)
    audit_log.log(job_id, "OCRCHECK_EDITED", {"path": rel})

    flags = ocr_check.detect(body.markdown)
    return {"flags": [f.as_dict() for f in flags], "saved": True}


@router.post("/api/jobs/{job_id}/normalize/approve")
def approve_normalized(job_id: str):
    """Mark normalization complete. Export will use normalized_md from here on."""
    job = _require_job(job_id)
    if job.status not in (JobStatus.normalizing, JobStatus.normalized, JobStatus.done):
        raise HTTPException(400, f"Job is not in normalization stage (status: {job.status})")
    if not job.normalized_md:
        raise HTTPException(400, "No normalized markdown to approve")

    job_store.update_status(job_id, JobStatus.normalized)
    audit_log.log(job_id, "OCRCHECK_APPROVED", {"path": job.normalized_md})
    return job_store.load(job_id).model_dump()


@router.post("/api/jobs/{job_id}/normalize/reopen")
def reopen_normalized(job_id: str):
    """Revert to normalizing so the Zone 1 user can continue editing."""
    job = _require_job(job_id)
    job_store.update_status(job_id, JobStatus.normalizing)
    audit_log.log(job_id, "OCRCHECK_REOPENED", {})
    return job_store.load(job_id).model_dump()


@router.post("/api/jobs/{job_id}/normalize/auto")
def auto_fix(job_id: str):
    """Apply all unambiguous OCR suggestions automatically."""
    job = _require_job(job_id)
    source = job.normalized_md or job.reviewed_md
    if not source:
        raise HTTPException(404, "No markdown available")
    text = (settings.jobs_dir.parent / source).read_text(encoding="utf-8")
    fixed, count = ocr_check.apply_suggestions(text)

    stem = Path(job.filename).stem
    norm_path = settings.normalized_dir / f"{stem}_normalized.md"
    norm_path.write_text(fixed, encoding="utf-8")
    rel = str(norm_path.relative_to(settings.jobs_dir.parent))
    job_store.update_status(job_id, JobStatus.normalizing, normalized_md=rel)
    audit_log.log(job_id, "OCRCHECK_AUTOFIX", {"fixes_applied": count})

    remaining = ocr_check.detect(fixed)
    return {"fixes_applied": count, "remaining_flags": len(remaining),
            "flags": [f.as_dict() for f in remaining]}


# ── Artifact import ───────────────────────────────────────────────────────────

def _import_stage_dirs() -> dict:
    return {
        "extracted":     (JobStatus.reviewing,   "extracted_md",  settings.extracted_dir),
        "pseudonymized": (JobStatus.normalizing,  "reviewed_md",   settings.reviewed_dir),
        "normalized":    (JobStatus.normalized,   "normalized_md", settings.normalized_dir),
    }


@router.post("/api/import")
async def import_artifact(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    stage: str = Form(...),
    src_lang: str = Form("de-DE"),
    tgt_lang: str = Form("en-GB"),
):
    """Import a markdown artifact at any pipeline stage and create a resumable job.

    stage values:
      "extracted"     → extracted markdown, enters entity review
      "pseudonymized" → pseudonymized markdown (stable tokens), enters OCRCheck
      "normalized"    → normalized markdown, ready for export
    """
    stages = _import_stage_dirs()
    if stage not in stages:
        raise HTTPException(400, f"Unknown stage '{stage}'. Choose: {list(stages)}")

    filename = file.filename or "imported.md"
    if not filename.lower().endswith(".md"):
        raise HTTPException(400, "Only .md files can be imported")

    content = (await file.read()).decode("utf-8")
    target_status, field_name, target_dir = stages[stage]

    # For pseudonymized imports: verify stable tokens are present
    if stage == "pseudonymized" and not _STABLE_TOKEN_RE.search(content):
        raise HTTPException(422, "Pseudonymized markdown must contain stable tokens like [PATIENT_NAME_001]")

    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / filename
    dest.write_text(content, encoding="utf-8")
    rel = str(dest.relative_to(settings.jobs_dir.parent))

    job = Job(filename=filename, src_lang=src_lang, tgt_lang=tgt_lang)
    job_store.save(job)
    kwargs = {field_name: rel}
    job_store.update_status(job.id, target_status, **kwargs)
    audit_log.log(job.id, "JOB_IMPORTED", {
        "stage": stage, "filename": filename, "status": target_status,
    })

    # For pseudonymized: auto-run OCR check in background
    if stage == "pseudonymized":
        background_tasks.add_task(_run_ocrcheck_background, job.id, content)

    return {"job_id": job.id, "status": target_status, "field": field_name, "path": rel}


async def _run_ocrcheck_background(job_id: str, text: str) -> None:
    job = job_store.load(job_id)
    if not job:
        return
    stem = Path(job.filename).stem
    norm_path = settings.normalized_dir / f"{stem}_normalized.md"
    norm_path.write_text(text, encoding="utf-8")
    rel = str(norm_path.relative_to(settings.jobs_dir.parent))
    job_store.update_status(job_id, JobStatus.normalizing, normalized_md=rel)
    audit_log.log(job_id, "OCRCHECK_STARTED", {"source": "import"})


# ── Export ───────────────────────────────────────────────────────────────────

@router.post("/api/jobs/{job_id}/export")
def trigger_export(
    job_id: str,
    reviewer:   str  = Form("human"),
    tgt_lang:   str  = Form(""),
    export_tmx: bool = Form(True),
    export_csv: bool = Form(True),
):
    """Zone 1: re-export is always allowed regardless of current status."""
    job = _require_job(job_id)
    if tgt_lang:
        job_store.update_status(job_id, job.status, tgt_lang=tgt_lang)
        job = job_store.load(job_id)
    try:
        job = export.run(job, reviewer=reviewer, export_tmx=export_tmx, export_csv=export_csv)
    except Exception as e:
        raise HTTPException(500, str(e))
    return job.model_dump()


@router.get("/api/jobs/{job_id}/md")
def get_extracted_md(job_id: str):
    job = _require_job(job_id)
    if not job.extracted_md:
        raise HTTPException(404, "No extracted markdown")
    path = settings.jobs_dir.parent / job.extracted_md
    if not path.exists():
        raise HTTPException(404, "File not found on disk")
    return HTMLResponse(path.read_text(encoding="utf-8"), media_type="text/plain")


@router.get("/api/jobs/{job_id}/pdf")
def get_original_pdf(job_id: str):
    """Serve the original PDF for inline viewing."""
    job = _require_job(job_id)
    pdf_path = settings.input_dir / job.filename
    if not pdf_path.exists():
        raise HTTPException(404, "Original PDF not found")
    return FileResponse(str(pdf_path), media_type="application/pdf",
                        headers={"Content-Disposition": "inline"})


@router.get("/api/jobs/{job_id}/preview")
def preview_info(job_id: str):
    """Return the number of available preview pages for this job."""
    _require_job(job_id)
    preview_dir = settings.zone1_previews_dir / job_id
    if not preview_dir.exists():
        return {"pages": 0}
    pages = sorted(preview_dir.glob("page_*.png"))
    return {"pages": len(pages)}


@router.get("/api/jobs/{job_id}/preview/{page}")
def preview_page(job_id: str, page: int):
    """Serve a single rendered page PNG (1-indexed)."""
    _require_job(job_id)
    png = settings.zone1_previews_dir / job_id / f"page_{page:03d}.png"
    if not png.exists():
        raise HTTPException(404, f"Preview page {page} not found")
    return FileResponse(str(png), media_type="image/png")


# ── Rehydration ──────────────────────────────────────────────────────────────

@router.post("/api/jobs/{job_id}/rehydrate")
def rehydrate(job_id: str, reviewer: str = Form("system")):
    """Reconstruct the reviewed markdown by replacing stable tokens with originals.

    Privileged operation — requires access to the encrypted mapping file.
    In production this endpoint must be restricted to the final_approver role.
    """
    job = _require_job(job_id)
    if job.status != JobStatus.done:
        raise HTTPException(400, f"Job must be in 'done' state to rehydrate (status: {job.status})")
    if not job.reviewed_md:
        raise HTTPException(400, "No reviewed markdown to rehydrate")

    token_map = mapping_store.load(job_id)
    if token_map is None:
        raise HTTPException(404, "Encrypted mapping not found for this job")

    reviewed_path = settings.jobs_dir.parent / job.reviewed_md
    if not reviewed_path.exists():
        raise HTTPException(404, "Reviewed markdown file not found on disk")

    pseudonymized_text = reviewed_path.read_text(encoding="utf-8")
    reconstructed = mapping_store.rehydrate(pseudonymized_text, token_map)

    stem = Path(job.reviewed_md).stem.replace("_reviewed", "")
    final_path = settings.final_dir / f"{stem}_final.md"
    final_path.write_text(reconstructed, encoding="utf-8")

    audit_log.log(job_id, "REHYDRATION_PERFORMED", {
        "actor": reviewer,
        "tokens_replaced": len(token_map),
        "output": str(final_path.relative_to(settings.jobs_dir.parent)),
    })

    return {
        "ok": True,
        "tokens_replaced": len(token_map),
        "final_path": str(final_path.relative_to(settings.jobs_dir.parent)),
    }


@router.get("/api/jobs/{job_id}/mapping/preview")
def mapping_preview(job_id: str):
    """Return the token mapping for a completed job (privileged — compliance reviewer).

    In production restrict to compliance_reviewer / final_approver roles.
    Returns tokens with original values for audit/verification purposes.
    """
    job = _require_job(job_id)
    token_map = mapping_store.load(job_id)
    if token_map is None:
        raise HTTPException(404, "No mapping found for this job")
    audit_log.log(job_id, "MAPPING_ACCESSED", {"token_count": len(token_map)})
    return {"job_id": job_id, "tokens": token_map}


@router.get("/api/jobs/{job_id}/download/{fmt}")
def download(job_id: str, fmt: str):
    job = _require_job(job_id)
    if fmt == "tmx" and job.exported_tmx:
        path = settings.jobs_dir.parent / job.exported_tmx
    elif fmt == "csv" and job.exported_csv:
        path = settings.jobs_dir.parent / job.exported_csv
    elif fmt == "md" and job.reviewed_md:
        path = settings.jobs_dir.parent / job.reviewed_md
    else:
        raise HTTPException(404, f"No {fmt} export available")
    if not path.exists():
        raise HTTPException(404, "File not found on disk")
    return FileResponse(path, filename=path.name)


# ── Policy Engine ────────────────────────────────────────────────────────────

from pydantic import BaseModel as _BM

class PolicyPrepareBody(_BM):
    job_id: str
    task: str
    strictness_mode: str = "max_allowable"
    consumer_type: str = "trusted_vendor"
    provider_risk: str = "trusted_vendor"


def _safe_policy_response(pkg) -> dict:
    """Return package summary with NO PHI, NO token_map."""
    from app.policy_engine.package import _build_risk_report, _build_usefulness_report, _build_transformation_log
    from app.policy_engine.profiles import load_profile

    profile = load_profile(pkg.profile)
    risk    = _build_risk_report(pkg)
    use_rep = _build_usefulness_report(pkg, type("R", (), {"task": pkg.profile})(), profile)
    log_rep = _build_transformation_log(pkg)

    resp: dict = {
        "package_id":          pkg.package_id,
        "job_id":              pkg.job_id,
        "profile":             pkg.profile,
        "selected_strictness": pkg.selected_strictness,
        "prepared_at":         pkg.prepared_at.isoformat(),
        "recommended_action":  pkg.manifest.get("recommended_action", "unknown"),
        "risk": {
            "risk_score":             risk["risk_score"],
            "direct_identifier_risk": risk["direct_identifier_risk"],
            "quasi_identifier_risk":  risk["quasi_identifier_risk"],
            "summary":                risk["summary"],
            "warnings":               risk["warnings"],
        },
        "usefulness": {
            "score":     use_rep["score"],
            "threshold": use_rep["threshold"],
            "passes":    use_rep["passes"],
            "status":    use_rep["status"],
            "summary":   use_rep["summary"],
            "weighted_checks": use_rep["weighted_checks"],
        },
        "transformation_summary": {
            "total":     log_rep["total_entities_processed"],
            "by_action": log_rep["by_action"],
        },
    }

    if pkg.max_allowable_decision:
        d = pkg.max_allowable_decision
        resp["max_allowable_decision"] = {
            "levels_tried":    d.levels_tried,
            "scores_by_level": {k: round(v, 3) for k, v in d.scores_by_level.items()},
            "selected":        d.selected,
            "reason":          d.reason,
            "provider_floor":  d.provider_floor,
        }

    return resp


@router.post("/api/policy/prepare")
def policy_prepare(body: PolicyPrepareBody):
    """Run the Policy Engine for a reviewed job. Returns report (no PHI, no mapping)."""
    from app.policy_engine.engine import prepare as pe_prepare
    from app.policy_engine.models import PolicyRequest

    job = _require_job(body.job_id)

    # Use original extracted markdown — must exist for offset-based transformation
    source_field = None
    for candidate in ("extracted_md", "reviewed_md", "normalized_md"):
        if getattr(job, candidate):
            source_field = candidate
            break

    if not source_field:
        raise HTTPException(400, "No source markdown available for this job")

    source_path = settings.jobs_dir.parent / getattr(job, source_field)
    if not source_path.exists():
        raise HTTPException(404, "Source markdown file not found on disk")

    document_text = source_path.read_text(encoding="utf-8")
    entities = [Entity.model_validate(e) for e in job.entities]

    request = PolicyRequest(
        job_id=job.id,
        task=body.task,
        strictness_mode=body.strictness_mode,
        consumer_type=body.consumer_type,
        provider_risk=body.provider_risk,
        document_text=document_text,
        entities=entities,
        source_language=job.src_lang,
        target_language=job.tgt_lang,
    )

    try:
        pkg = pe_prepare(request, save=True)
    except Exception as e:
        raise HTTPException(500, f"Policy engine error: {e}")

    audit_log.log(job.id, "POLICY_PACKAGE_CREATED_VIA_UI", {
        "package_id": pkg.package_id,
        "task": body.task,
        "strictness": pkg.selected_strictness,
    })

    return _safe_policy_response(pkg)


@router.get("/api/policy/packages/{package_id}/report")
def policy_report(package_id: str):
    """Return the full report for a prepared package (no PHI, no mapping)."""
    from app.policy_engine.package import _packages_dir
    import json as _json

    pkg_dir = _packages_dir() / package_id
    if not pkg_dir.exists():
        raise HTTPException(404, "Package not found")

    def _read_json(name):
        p = pkg_dir / name
        return _json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    preview_md = (pkg_dir / "preview.md").read_text(encoding="utf-8") \
        if (pkg_dir / "preview.md").exists() else ""
    prepared_md = (pkg_dir / "prepared.md").read_text(encoding="utf-8") \
        if (pkg_dir / "prepared.md").exists() else ""

    return {
        "package_id":       package_id,
        "manifest":         _read_json("manifest.json"),
        "risk_report":      _read_json("risk_report.json"),
        "usefulness_report":_read_json("usefulness_report.json"),
        "transformation_log":_read_json("transformation_log.json"),
        "preview_md":       preview_md,
        "prepared_md":      prepared_md,   # Zone 2 safe — already anonymized
    }


@router.get("/api/policy/packages/{package_id}/download")
def policy_download(package_id: str):
    """Download the prepared package ZIP (Zone 2 safe — no mapping, no original PHI)."""
    from app.policy_engine.package import _packages_dir, zip_package

    pkg_dir = _packages_dir() / package_id
    if not pkg_dir.exists():
        raise HTTPException(404, "Package not found")

    zip_path = pkg_dir.parent / f"{package_id}.zip"
    if not zip_path.exists():
        zip_path = zip_package(pkg_dir)

    return FileResponse(
        str(zip_path),
        filename=f"confidoc_package_{package_id}.zip",
        media_type="application/zip",
    )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _require_job(job_id: str) -> Job:
    job = job_store.load(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


def _update_entity(job: Job, entity_id: str, **updates) -> Job:
    updated = []
    found = False
    for e in job.entities:
        entity = Entity.model_validate(e)
        if entity.id == entity_id:
            for k, v in updates.items():
                setattr(entity, k, v)
            found = True
        updated.append(entity.model_dump())
    if not found:
        raise HTTPException(404, "Entity not found")
    job_store.update_status(job.id, job.status, entities=updated)
    return job_store.load(job.id)
