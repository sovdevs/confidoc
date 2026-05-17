"""Review UI routes — FastAPI router for the HITL anonymization interface."""

import asyncio
import json
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
from app.services import demo_capture

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
        # Demo capture: extraction + auto-entity snapshot (no-ops unless demo run active)
        demo_capture.capture_extraction(job.id, job, pdf_bytes)
        demo_capture.capture_auto_entities(job.id, job)
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
    demo_capture.log_entity_event(
        job_id, "add", new_entity.id,
        new_label=label,
        new_offsets=(start, end),
        source="manual",
        safe_display=replacement,  # user-supplied token, not PHI
    )
    return job_store.load(job_id).model_dump()


# ── Review actions ───────────────────────────────────────────────────────────

@router.post("/api/jobs/{job_id}/entities/{entity_id}/approve")
def approve_entity(job_id: str, entity_id: str):
    job = _require_job(job_id)
    # Capture before state for event log
    old_entity = next((Entity.model_validate(e) for e in job.entities if e.id == entity_id), None)
    job = _update_entity(job, entity_id, approved=True)
    audit_log.log(job_id, "entity_approved", {"entity_id": entity_id})
    if old_entity:
        demo_capture.log_entity_event(
            job_id, "approve", entity_id,
            old_label=old_entity.label, new_label=old_entity.label,
            old_offsets=(old_entity.start, old_entity.end),
            source="manual",
            safe_display=old_entity.replacement or old_entity.label,
        )
    return {"ok": True}


@router.post("/api/jobs/{job_id}/entities/{entity_id}/dismiss")
def dismiss_entity(job_id: str, entity_id: str):
    job = _require_job(job_id)
    old_entity = next((Entity.model_validate(e) for e in job.entities if e.id == entity_id), None)
    job = _update_entity(job, entity_id, approved=False, dismissed=True)
    audit_log.log(job_id, "entity_dismissed", {"entity_id": entity_id})
    if old_entity:
        demo_capture.log_entity_event(
            job_id, "reject", entity_id,
            old_label=old_entity.label, new_label=old_entity.label,
            old_offsets=(old_entity.start, old_entity.end),
            source="manual",
            safe_display=old_entity.label,
        )
    return {"ok": True}


@router.delete("/api/jobs/{job_id}/entities/{entity_id}")
def delete_entity(job_id: str, entity_id: str):
    """Permanently remove an entity so the text span can be re-annotated."""
    job = _require_job(job_id)
    old_entity = next((Entity.model_validate(e) for e in job.entities if e.id == entity_id), None)
    before = len(job.entities)
    job.entities = [e for e in job.entities if e.id != entity_id]
    if len(job.entities) == before:
        raise HTTPException(404, f"Entity {entity_id} not found")
    job_store.save(job)
    audit_log.log(job_id, "entity_deleted", {"entity_id": entity_id})
    if old_entity:
        demo_capture.log_entity_event(
            job_id, "delete", entity_id,
            old_label=old_entity.label,
            old_offsets=(old_entity.start, old_entity.end),
            source="manual",
            safe_display=old_entity.label,
        )
    return {"ok": True}


@router.post("/api/jobs/{job_id}/entities/{entity_id}/edit")
def edit_entity(job_id: str, entity_id: str, replacement: Annotated[str, Form()]):
    job = _require_job(job_id)
    old_entity = next((Entity.model_validate(e) for e in job.entities if e.id == entity_id), None)
    job = _update_entity(job, entity_id, approved=True, replacement=replacement, edited=True)
    audit_log.log(job_id, "entity_edited", {"entity_id": entity_id, "replacement": replacement})
    if old_entity:
        demo_capture.log_entity_event(
            job_id, "edit_text", entity_id,
            old_label=old_entity.label, new_label=old_entity.label,
            old_offsets=(old_entity.start, old_entity.end),
            source="manual",
            safe_display=replacement,  # replacement is user-supplied token label, not PHI
        )
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
    demo_capture.log_entity_event(
        job_id, "approve_all", "all",
        safe_display=f"{len(updated)} entities approved",
    )
    demo_capture.capture_review_final(job_id, job_store.load(job_id))
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


# ── Rehydration (Zone 1 only) ─────────────────────────────────────────────────

_REHYDRATE_EXTS = {".md", ".xliff", ".sdlxliff", ".tmx", ".docx"}
_REHYDRATE_MEDIA = {
    ".md":        "text/markdown",
    ".xliff":     "application/xliff+xml",
    ".sdlxliff":  "application/xliff+xml",
    ".tmx":       "application/xml",
    ".docx":      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@router.post("/api/rehydrate/info")
async def rehydrate_info(file: UploadFile = File(...)):
    """Probe a file to extract its embedded package_id without rehydrating.

    Zone 1 only — used by the UI to preview detected package before committing.
    """
    import json as _json
    from app.services.rehydrate_parser import extract_package_id
    from app.policy_engine.package import _packages_dir

    content = await file.read()
    filename = file.filename or "uploaded"
    ext = Path(filename).suffix.lower()

    if ext not in _REHYDRATE_EXTS:
        return {"found": False, "error": f"Unsupported type '{ext}'"}

    package_id = extract_package_id(content, filename)
    if not package_id:
        return {"found": False, "package_id": None}

    pkg_dir = _packages_dir() / package_id
    if not pkg_dir.exists():
        return {"found": False, "package_id": package_id,
                "error": "Package not found on this server"}

    manifest_path = pkg_dir / "manifest.json"
    if not manifest_path.exists():
        return {"found": False, "package_id": package_id, "error": "Manifest missing"}

    m = _json.loads(manifest_path.read_text(encoding="utf-8"))
    return {
        "found":       True,
        "package_id":  package_id,
        "job_id":      m.get("job_id"),
        "task":        m.get("task"),
        "profile":     m.get("profile"),
        "prepared_at": m.get("prepared_at"),
    }


@router.post("/api/rehydrate")
async def rehydrate_from_export(file: UploadFile = File(...)):
    """Zone 1 only — replace stable tokens with original PHI.

    The uploaded file must be a Confidoc export with an embedded package_id.
    Decrypts the per-job mapping (Zone 1 resource) and substitutes tokens.
    Returns the rehydrated file as a download.

    DOCX: token substitution is run-by-run; formatting may be partially
    preserved but is not guaranteed across all editors.
    """
    import json as _json
    from fastapi.responses import Response
    from app.services.rehydrate_parser import extract_package_id, rehydrate_content
    from app.policy_engine.package import _packages_dir

    content = await file.read()
    filename = file.filename or "uploaded"
    ext = Path(filename).suffix.lower()

    if ext not in _REHYDRATE_EXTS:
        raise HTTPException(400,
            f"Unsupported file type '{ext}'. "
            f"Accepted: {', '.join(sorted(_REHYDRATE_EXTS))}")

    # Extract embedded package_id
    package_id = extract_package_id(content, filename)
    if not package_id:
        raise HTTPException(422,
            "No Confidoc package ID found in this file. "
            "Only files exported by Confidoc (with embedded package_id) can be rehydrated here. "
            "DOCX files may lose the package ID if re-saved by an external editor — "
            "use the original Confidoc-generated DOCX.")

    # Locate package manifest
    pkg_dir = _packages_dir() / package_id
    if not pkg_dir.exists():
        raise HTTPException(404, f"Package '{package_id}' not found on this server.")

    manifest_path = pkg_dir / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(500, "Package manifest missing")

    manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
    job_id = manifest.get("job_id")
    if not job_id:
        raise HTTPException(500, "Package manifest missing job_id")

    # Load the policy token map — stored inside the package dir so it
    # always co-locates with the package files and survives restarts.
    token_map = None
    token_map_path = pkg_dir / "token_map.enc"
    if token_map_path.exists():
        try:
            import json as _json
            from cryptography.fernet import Fernet as _Fernet
            from app.storage.mappings import _get_key as _mk
            payload = _Fernet(_mk()).decrypt(token_map_path.read_bytes())
            token_map = _json.loads(payload.decode())["tokens"]
        except Exception:
            token_map = None
    # Fallback: legacy mappings-dir location (older packages) or Zone 1 map
    if token_map is None:
        token_map = mapping_store.load(f"pkg_{package_id}") or mapping_store.load(job_id)
    if token_map is None:
        raise HTTPException(404,
            "Encrypted token mapping not found for this job. "
            "Rehydration requires the Zone 1 MAPPING_KEY.")

    # Apply token substitution
    try:
        rehydrated = rehydrate_content(content, filename, token_map)
    except Exception as exc:
        raise HTTPException(500, f"Rehydration failed: {exc}")

    stem = Path(filename).stem
    final_name = f"{stem}_rehydrated{ext}"
    final_path = settings.final_dir / final_name
    final_path.write_bytes(rehydrated)

    audit_log.log(job_id, "REHYDRATION_FROM_EXPORT", {
        "package_id":      package_id,
        "filename":        filename,
        "tokens_replaced": len(token_map),
        "output":          str(final_path.relative_to(settings.jobs_dir.parent)),
    })

    # Demo capture: save rehydration artifacts
    demo_capture.capture_rehydration(job_id, content, filename, rehydrated, token_map)

    return Response(
        content=rehydrated,
        media_type=_REHYDRATE_MEDIA.get(ext, "application/octet-stream"),
        headers={
            "Content-Disposition":          f'attachment; filename="{final_name}"',
            "X-Confidoc-Job-Id":            job_id,
            "X-Confidoc-Package-Id":        package_id,
            "X-Confidoc-Tokens-Replaced":   str(len(token_map)),
        },
    )


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
    tgt_lang: str = ""   # required for translation task (TMX generation)


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

    if body.task == "translation" and not body.tgt_lang:
        raise HTTPException(400, "Target language (tgt_lang) is required for translation packages")

    try:
        pkg = pe_prepare(request, save=True)
    except Exception as e:
        raise HTTPException(500, f"Policy engine error: {e}")

    # Save the policy token map inside the package directory so it always
    # co-locates with the package files. Storing it separately in mappings/
    # risks losing it if the container restarts between prepare and rehydrate.
    from app.policy_engine.package import _packages_dir
    if pkg.policy_token_map:
        import json as _json
        from cryptography.fernet import Fernet as _Fernet
        from app.storage.mappings import _get_key as _mk
        _pkg_token_path = _packages_dir() / pkg.package_id / "token_map.enc"
        _payload = _json.dumps({"tokens": pkg.policy_token_map}, ensure_ascii=False).encode()
        _pkg_token_path.write_bytes(_Fernet(_mk()).encrypt(_payload))

    # Generate supplementary export files inside the package directory
    from pdf_to_markdown.exporter import md_to_segments, write_tmx
    from app.services.xliff_export import xliff_12, sdlxliff_12
    from app.services.docx_export import md_to_docx
    import csv as _csv

    pkg_dir = _packages_dir() / pkg.package_id
    prepared_path = pkg_dir / "prepared.md"
    stem = Path(job.filename).stem

    if prepared_path.exists():
        prepared_md = prepared_path.read_text(encoding="utf-8")

        # Embed package_id in prepared.md as HTML comment (first line)
        pkg_marker = f"<!-- confidoc-package:{pkg.package_id} -->"
        if not prepared_md.startswith(pkg_marker):
            prepared_md = f"{pkg_marker}\n{prepared_md}"
            prepared_path.write_text(prepared_md, encoding="utf-8")

        segments = md_to_segments(prepared_md)
        tgt = body.tgt_lang or job.tgt_lang or "und"

        # CSV (source segments) — all packages
        csv_path = pkg_dir / f"{stem}.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = _csv.writer(f, quoting=_csv.QUOTE_ALL)
            writer.writerow(["id", job.src_lang])
            for i, seg in enumerate(segments, 1):
                writer.writerow([i, seg])

        if body.task == "translation":
            # TMX — bilingual translation memory (patch to embed package_id)
            tmx_path = pkg_dir / f"{stem}.tmx"
            write_tmx(segments, job.src_lang, tgt, tmx_path)
            tmx_text = tmx_path.read_text(encoding="utf-8")
            tmx_text = tmx_text.replace(
                '<?xml version="1.0" encoding="utf-8"?>',
                f'<?xml version="1.0" encoding="utf-8"?>\n<!-- confidoc-package:{pkg.package_id} -->',
                1,
            )
            tmx_path.write_text(tmx_text, encoding="utf-8")

            # XLIFF 1.2 — generic CAT tool format
            (pkg_dir / f"{stem}.xliff").write_bytes(
                xliff_12(segments, job.src_lang, tgt, original=f"{stem}.md",
                         package_id=pkg.package_id)
            )

            # SDL XLIFF — Trados Studio compatible
            (pkg_dir / f"{stem}.sdlxliff").write_bytes(
                sdlxliff_12(segments, job.src_lang, tgt, original=f"{stem}.md",
                            package_id=pkg.package_id)
            )

            # DOCX — Word document for translators
            (pkg_dir / f"{stem}.docx").write_bytes(
                md_to_docx(prepared_md, job.src_lang, tgt, title=stem,
                           package_id=pkg.package_id)
            )

        # Regenerate ZIP so it includes all export files
        from app.policy_engine.package import zip_package
        zip_package(pkg_dir)

    audit_log.log(job.id, "POLICY_PACKAGE_CREATED_VIA_UI", {
        "package_id": pkg.package_id,
        "task": body.task,
        "strictness": pkg.selected_strictness,
        "tgt_lang": body.tgt_lang or None,
    })

    # Demo capture: copy package into demo run folder
    demo_capture.capture_export_package(job.id, pkg_dir, {
        "package_id":      pkg.package_id,
        "task":            body.task,
        "consumer_type":   body.consumer_type,
        "provider_risk":   body.provider_risk,
        "strictness_mode": body.strictness_mode,
        "tgt_lang":        body.tgt_lang or None,
        "profile":         pkg.profile,
        "selected_strictness": pkg.selected_strictness,
        "recommended_action":  pkg.manifest.get("recommended_action", ""),
    })

    return _safe_policy_response(pkg)


@router.get("/api/jobs/{job_id}/packages")
def list_job_packages(job_id: str):
    """Return all export packages created for this job, newest first."""
    import json as _json
    from app.policy_engine.package import _packages_dir

    _require_job(job_id)
    pkgs_dir = _packages_dir()
    if not pkgs_dir.exists():
        return {"packages": []}

    results = []
    for pkg_dir in pkgs_dir.iterdir():
        if not pkg_dir.is_dir():
            continue
        manifest_path = pkg_dir / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            m = _json.loads(manifest_path.read_text(encoding="utf-8"))
            if m.get("job_id") != job_id:
                continue
            results.append({
                "package_id":         pkg_dir.name,
                "created_at":         m.get("prepared_at", ""),
                "task":               m.get("task", ""),
                "profile":            m.get("profile", ""),
                "strictness":         m.get("strictness", ""),
                "consumer_type":      m.get("consumer_type", ""),
                "provider_risk":      m.get("provider_risk", ""),
                "recommended_action": m.get("recommended_action", ""),
                "usefulness_score":   m.get("clinical_facts_preserved_score"),
                "risk_level":         m.get("risk_score", ""),
            })
        except Exception:
            continue

    results.sort(key=lambda x: x["created_at"], reverse=True)
    return {"packages": results}


@router.get("/api/policy/packages/{package_id}/files")
def package_files(package_id: str):
    """List downloadable files inside a package (Zone 2 safe files only)."""
    from app.policy_engine.package import _packages_dir
    _SAFE_EXTS = {".md", ".tmx", ".xliff", ".sdlxliff", ".docx", ".csv", ".zip"}
    _SKIP = {"preview.md"}  # internal only; full content in /report
    pkg_dir = _packages_dir() / package_id
    if not pkg_dir.exists():
        raise HTTPException(404, "Package not found")
    files = []
    for p in sorted(pkg_dir.iterdir()):
        if p.suffix in _SAFE_EXTS and p.name not in _SKIP and p.is_file():
            files.append({"name": p.name, "size": p.stat().st_size})
    return {"files": files}


@router.get("/api/policy/packages/{package_id}/files/{filename}")
def package_file_download(package_id: str, filename: str):
    """Download a specific file from a package."""
    from app.policy_engine.package import _packages_dir
    _SAFE_EXTS = {".md", ".tmx", ".xliff", ".sdlxliff", ".docx", ".csv", ".json", ".zip"}
    pkg_dir = _packages_dir() / package_id
    if not pkg_dir.exists():
        raise HTTPException(404, "Package not found")
    # Prevent path traversal
    file_path = (pkg_dir / filename).resolve()
    if not file_path.is_relative_to(pkg_dir.resolve()):
        raise HTTPException(400, "Invalid filename")
    if not file_path.exists() or file_path.suffix not in _SAFE_EXTS:
        raise HTTPException(404, "File not found")
    media_types = {
        ".md": "text/markdown", ".tmx": "application/xml",
        ".xliff": "application/xliff+xml", ".sdlxliff": "application/xliff+xml",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".csv": "text/csv", ".json": "application/json", ".zip": "application/zip",
    }
    return FileResponse(str(file_path),
                        media_type=media_types.get(file_path.suffix, "application/octet-stream"),
                        filename=filename)


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


# ── Demo capture API ─────────────────────────────────────────────────────────

def _require_demo_mode() -> None:
    if not settings.demo_capture:
        raise HTTPException(403, "Demo capture mode is not enabled (set CONFIDOC_DEMO_CAPTURE=true)")


@router.get("/api/demo/inputs")
def list_demo_inputs():
    """List synthetic demo PDF documents available in data/demo/."""
    _require_demo_mode()
    pdfs = sorted(p.name for p in settings.demo_dir.glob("*.pdf")) if settings.demo_dir.exists() else []
    return {"files": pdfs}


@router.post("/api/demo/inputs/{filename}/process")
async def process_demo_input(
    background_tasks: BackgroundTasks,
    filename: str,
    src_lang: str = Form("de-DE"),
    tgt_lang: str = Form("en-GB"),
    pdf_provider: str = Form(""),
    pdf_model: str = Form(""),
    pdf_api_key: str = Form(""),
):
    """Load a demo PDF using pre-captured artifacts (no LLM call required)."""
    _require_demo_mode()
    pdf_path = settings.demo_dir / filename
    if not pdf_path.exists():
        raise HTTPException(404, f"{filename} not found in demo directory")
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(400, "PDF files only.")

    playback_dir = demo_capture.find_playback(filename)
    if playback_dir is None:
        raise HTTPException(500, "No pre-captured demo run found for this document.")

    job = Job(filename=filename, src_lang=src_lang, tgt_lang=tgt_lang)
    job_store.save(job)
    audit_log.log(job.id, "job_created", {"filename": filename, "source": "demo_playback"})

    # Copy pre-captured extracted markdown into the normal extracted dir
    src_md = playback_dir / "01_extraction" / "extracted.md"
    dest_md = settings.extracted_dir / (Path(filename).stem + f"_{job.id}.md")
    settings.extracted_dir.mkdir(parents=True, exist_ok=True)
    import shutil as _shutil
    _shutil.copy2(src_md, dest_md)
    rel_md = str(dest_md.relative_to(settings.jobs_dir.parent))

    # Load pre-captured entities
    entities_path = playback_dir / "02_auto_entities" / "entities_auto.json"
    raw = json.loads(entities_path.read_text(encoding="utf-8"))
    from app.storage.jobs import Entity
    entities = [Entity.model_validate(e) for e in raw.get("entities", [])]

    job_store.update_status(job.id, JobStatus.reviewing,
                            extracted_md=rel_md, entities=entities)

    run_id = demo_capture.start_demo_run(job.id, label=f"Demo playback: {filename}")
    audit_log.log(job.id, "DEMO_PLAYBACK_LOADED", {"demo_run_id": run_id, "source": str(playback_dir)})

    return {"job_id": job.id, "demo_run_id": run_id, "status": JobStatus.reviewing}


@router.post("/api/demo/start")
def demo_start(job_id: str = Form(...), label: str = Form("")):
    """Attach demo capture to an existing job. Creates a new demo_run_id."""
    _require_demo_mode()
    _require_job(job_id)
    run_id = demo_capture.start_demo_run(job_id, label=label)
    audit_log.log(job_id, "DEMO_CAPTURE_STARTED", {"demo_run_id": run_id, "label": label})

    # Snapshot current state for the job (extraction + entities may already exist)
    job = job_store.load(job_id)
    demo_capture.capture_extraction(job_id, job)
    if job.entities:
        demo_capture.capture_auto_entities(job_id, job)

    return {"demo_run_id": run_id, "job_id": job_id}


@router.post("/api/demo/stop")
def demo_stop(job_id: str = Form(...)):
    """Detach demo capture from a job (artifacts are preserved)."""
    _require_demo_mode()
    run_id = demo_capture.get_demo_run_id(job_id)
    demo_capture.stop_demo_run(job_id)
    audit_log.log(job_id, "DEMO_CAPTURE_STOPPED", {"demo_run_id": run_id})
    return {"ok": True, "demo_run_id": run_id}


@router.get("/api/demo/status")
def demo_status():
    """Return active demo sessions and recent demo runs."""
    _require_demo_mode()
    from app.services import demo_capture as _dc
    sessions = _dc._load_sessions()
    runs = _dc.list_demo_runs()[:10]   # last 10 runs
    return {"active_sessions": sessions, "recent_runs": runs}


@router.get("/api/demo/runs/{demo_run_id}/files")
def demo_run_files(demo_run_id: str):
    """List all captured files for a demo run (by stage)."""
    _require_demo_mode()
    run_dir = settings.demo_runs_dir / demo_run_id
    if not run_dir.exists():
        raise HTTPException(404, "Demo run not found")
    stages = {}
    for stage_dir in sorted(run_dir.iterdir()):
        if stage_dir.is_dir():
            stages[stage_dir.name] = [
                {"name": f.name, "size": f.stat().st_size}
                for f in sorted(stage_dir.iterdir()) if f.is_file()
            ]
    meta_path = run_dir / "run_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    return {"demo_run_id": demo_run_id, "meta": meta, "stages": stages}


# ── LLM Export ───────────────────────────────────────────────────────────────

@router.get("/api/llm-export/prompts")
def list_llm_export_prompts():
    """Return available saved prompt files from data/llm_export_prompts/."""
    from app.services.prompt_loader import load_prompts
    return {"prompts": load_prompts()}


class LLMExportRunBody(BaseModel):
    prompt_mode: str = "saved"          # saved | ad_hoc | saved_plus_ad_hoc
    prompt_id: str | None = None
    ad_hoc_prompt: str | None = None
    provider: str | None = None
    model: str | None = None
    api_key: str | None = None
    source_type: str = "reviewed_markdown"  # reviewed_markdown | policy_package
    package_id: str | None = None


@router.post("/api/llm-export/{job_id}/run")
async def run_llm_export(job_id: str, body: LLMExportRunBody):
    """Create an LLM export package and run it against the selected model.

    source_type = "reviewed_markdown": uses normalized_md or reviewed_md (Zone 2 safe).
    source_type = "policy_package":    uses prepared.md from a specific policy package.
    """
    import uuid
    from datetime import datetime
    from app.services.prompt_loader import get_prompt_by_id
    from app.services.llm_adapter import llm_export_complete

    job = _require_job(job_id)

    # --- Resolve markdown source ---
    if body.source_type == "policy_package":
        if not body.package_id:
            raise HTTPException(400, "package_id required when source_type is policy_package")
        prepared_md = settings.prepared_packages_dir / body.package_id / "prepared.md"
        if not prepared_md.exists():
            raise HTTPException(404, "Prepared package markdown not found")
        document = prepared_md.read_text(encoding="utf-8")
        privacy_level = "policy_package"
    else:
        md_path = Path(job.normalized_md) if job.normalized_md else (
            Path(job.reviewed_md) if job.reviewed_md else None
        )
        if not md_path or not md_path.exists():
            raise HTTPException(400, "No approved pseudonymized markdown available for this job")
        document = md_path.read_text(encoding="utf-8")
        privacy_level = "pii_public"

    # --- Assemble prompt ---
    saved_text = ""
    prompt_name = ""
    task_type = "custom"

    if body.prompt_id:
        p = get_prompt_by_id(body.prompt_id)
        if not p:
            raise HTTPException(404, f"Prompt '{body.prompt_id}' not found")
        saved_text = p["prompt_text"]
        prompt_name = p["title"]
        task_type = p["task_type"]

    ad_hoc = (body.ad_hoc_prompt or "").strip()

    if body.prompt_mode == "saved":
        combined = saved_text
    elif body.prompt_mode == "ad_hoc":
        combined = ad_hoc
    else:  # saved_plus_ad_hoc
        combined = saved_text + (f"\n\nAdditional instruction:\n{ad_hoc}" if ad_hoc else "")

    if not combined.strip():
        raise HTTPException(400, "No prompt provided")

    # --- LLM selection ---
    provider = body.provider or settings.anon_provider
    model    = body.model    or settings.anon_model
    api_key  = body.api_key  or settings.anon_api_key

    # --- Build and persist the run record ---
    run_id     = uuid.uuid4().hex[:12]
    created_at = datetime.utcnow().isoformat() + "Z"

    run_record: dict = {
        "run_id":        run_id,
        "job_id":        job_id,
        "source_type":   body.source_type,
        "package_id":    body.package_id,
        "prompt_id":     body.prompt_id,
        "prompt_name":   prompt_name,
        "prompt_text":   saved_text,
        "ad_hoc_prompt": ad_hoc,
        "prompt_mode":   body.prompt_mode,
        "combined_prompt": combined,
        "task_type":     task_type,
        "provider":      provider,
        "model":         model,
        "rag_enabled":   False,
        "tools_allowed": False,
        "rag_scope":     None,
        "privacy_level": privacy_level,
        "output_text":   None,
        "status":        "running",
        "error":         None,
        "created_at":    created_at,
    }

    run_dir = settings.llm_runs_dir / job_id
    run_dir.mkdir(parents=True, exist_ok=True)
    run_path = run_dir / f"{run_id}.json"
    run_path.write_text(json.dumps(run_record, indent=2, ensure_ascii=False), encoding="utf-8")

    try:
        output = await llm_export_complete(
            prompt=combined,
            document=document,
            provider=provider,
            model=model,
            api_key=api_key,
        )
        run_record["output_text"] = output
        run_record["status"]      = "completed"
        audit_log.log(job_id, "LLM_EXPORT_RUN", {
            "run_id": run_id, "prompt_id": body.prompt_id,
            "source_type": body.source_type, "provider": provider, "model": model,
        })
    except Exception as exc:
        run_record["status"] = "failed"
        run_record["error"]  = str(exc)
        audit_log.log(job_id, "LLM_EXPORT_FAILED", {"run_id": run_id, "error": str(exc)})
    finally:
        run_path.write_text(json.dumps(run_record, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "run_id":      run_record["run_id"],
        "status":      run_record["status"],
        "output_text": run_record["output_text"],
        "error":       run_record["error"],
        "provider":    provider,
        "model":       model,
        "prompt_name": prompt_name,
        "task_type":   task_type,
        "created_at":  created_at,
    }


@router.get("/api/llm-export/{job_id}/runs")
def list_llm_runs(job_id: str):
    """List previous LLM export runs for a job, newest first."""
    run_dir = settings.llm_runs_dir / job_id
    if not run_dir.exists():
        return {"runs": []}
    runs = []
    for path in sorted(run_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            runs.append({
                "run_id":        data.get("run_id"),
                "status":        data.get("status"),
                "prompt_id":     data.get("prompt_id"),
                "prompt_name":   data.get("prompt_name"),
                "task_type":     data.get("task_type"),
                "provider":      data.get("provider"),
                "model":         data.get("model"),
                "source_type":   data.get("source_type"),
                "privacy_level": data.get("privacy_level"),
                "created_at":    data.get("created_at"),
                "output_text":   data.get("output_text"),
                "error":         data.get("error"),
            })
        except Exception:
            continue
    return {"runs": runs}


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
