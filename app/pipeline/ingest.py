"""Ingest stage: save uploaded PDF and run pdf2md extraction."""

import asyncio
import shutil
from pathlib import Path

from pdf_to_markdown.config import load_settings as load_pdf_settings
from pdf_to_markdown.pipeline import run_batch

from app.config import settings
from app.storage import audit_log, jobs as job_store
from app.storage.jobs import Job, JobStatus


async def run(job: Job, pdf_bytes: bytes) -> Job:
    pdf_path = settings.input_dir / job.filename
    pdf_path.write_bytes(pdf_bytes)
    audit_log.log(job.id, "pdf_saved", {"path": str(pdf_path)})

    job_store.update_status(job.id, JobStatus.extracting)
    audit_log.log(job.id, "extraction_started")

    out_dir = settings.extracted_dir
    retry_dir = settings.extracted_dir / "retry"

    pdf_settings = load_pdf_settings(
        input_dir=settings.input_dir,
        output_dir=out_dir,
        retry_dir=retry_dir,
        max_concurrent_pdfs=settings.max_concurrent_pdfs,
    )

    try:
        summary = await run_batch([pdf_path], pdf_settings)
    except Exception as e:
        job_store.update_status(job.id, JobStatus.failed, error=str(e))
        audit_log.log(job.id, "extraction_failed", {"error": str(e)})
        raise

    if job.filename.replace(".pdf", "") in [f.stem for f in summary.get("failed", [])]:
        err = "pdf2md reported failure"
        job_store.update_status(job.id, JobStatus.failed, error=err)
        audit_log.log(job.id, "extraction_failed", {"error": err})
        raise RuntimeError(err)

    md_path = out_dir / (Path(job.filename).stem + ".md")
    rel = str(md_path.relative_to(settings.jobs_dir.parent))
    job_store.update_status(job.id, JobStatus.reviewing, extracted_md=rel)
    audit_log.log(job.id, "extraction_done", {"md": rel})

    return job_store.load(job.id)
