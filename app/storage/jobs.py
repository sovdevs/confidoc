"""File-backed job store. Each job is a JSON file in data/jobs/."""

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from app.config import settings


class JobStatus(str, Enum):
    imported = "imported"        # pulled from server source; awaiting explicit user action
    processing = "processing"    # user triggered processing; pipeline starting
    pending = "pending"          # uploaded, not yet processed
    extracting = "extracting"    # pdf2md running
    reviewing = "reviewing"      # awaiting HITL anonymization review
    approved = "approved"        # anonymization approved; ready for normalization or export
    normalizing = "normalizing"  # OCRCheck / markdown normalization in progress
    normalized = "normalized"    # normalization approved; ready for export
    exporting = "exporting"      # export running
    done = "done"                # complete
    failed = "failed"


class Entity(BaseModel):
    """A detected PII entity."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    label: str        # e.g. "PATIENT_NAME", "DATE", "ADDRESS"
    text: str         # original matched text
    start: int        # char offset in the extracted markdown
    end: int
    replacement: str  # proposed replacement token, e.g. "[PATIENT_NAME]"
    approved: bool = False
    dismissed: bool = False   # True = reviewer explicitly rejected this entity
    edited: bool = False
    manual: bool = False      # True when added by a human reviewer (not auto-detected)


class Job(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    filename: str
    status: JobStatus = JobStatus.pending
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    extracted_md: Optional[str] = None    # data/extracted/
    anonymized_md: Optional[str] = None
    reviewed_md: Optional[str] = None    # data/reviewed/   — pseudonymized
    normalized_md: Optional[str] = None  # data/normalized/ — after OCRCheck
    exported_tmx: Optional[str] = None
    exported_csv: Optional[str] = None
    mapping_path: Optional[str] = None   # data/mappings/   — encrypted token map

    # Server ingest provenance (set for imported jobs only)
    ingest_source_id: Optional[str] = None
    ingest_source_type: Optional[str] = None
    ingest_remote_path: Optional[str] = None
    requires_ocr: bool = True           # False for docx/txt/md — skip OCR pipeline

    # OCR extraction model used for this job (may differ from config defaults)
    pdf_provider: Optional[str] = None
    pdf_model: Optional[str] = None
    page_count: Optional[int] = None     # set as soon as pages are counted

    src_lang: str = "de-DE"
    tgt_lang: str = "en-GB"

    entities: list[Entity] = []
    notes: str = ""
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    error: Optional[str] = None

    # Lightweight per-job workflow state for optional/skippable steps.
    # Keys: "ocr_check", "reports" — values: "pending" | "complete" | "skipped"
    workflow_state: dict = {}

    # Precise activity timestamps — not proxied from updated_at
    last_entity_action_at:      Optional[datetime] = None
    last_ocr_check_approved_at: Optional[datetime] = None


def _path(job_id: str) -> Path:
    return settings.jobs_dir / f"{job_id}.json"


def save(job: Job) -> None:
    job.updated_at = datetime.now(timezone.utc)
    _path(job.id).write_text(job.model_dump_json(indent=2), encoding="utf-8")


def load(job_id: str) -> Optional[Job]:
    p = _path(job_id)
    if not p.exists():
        return None
    job = Job.model_validate_json(p.read_text(encoding="utf-8"))
    # Ensure entities are Entity instances — Python 3.14 / Pydantic Rust serializer compat
    job.entities = [Entity.model_validate(e) if isinstance(e, dict) else e for e in job.entities]
    return job


def list_all() -> list[Job]:
    jobs = []
    for p in settings.jobs_dir.glob("*.json"):
        try:
            jobs.append(Job.model_validate_json(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    jobs.sort(key=lambda j: j.created_at or "", reverse=True)
    return jobs


def update_status(job_id: str, status: JobStatus, **kwargs) -> Optional[Job]:
    job = load(job_id)
    if not job:
        return None
    job.status = status
    for k, v in kwargs.items():
        setattr(job, k, v)
    save(job)
    return job
