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

    src_lang: str = "de-DE"
    tgt_lang: str = "en-GB"

    entities: list[Entity] = []
    notes: str = ""
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    error: Optional[str] = None


def _path(job_id: str) -> Path:
    return settings.jobs_dir / f"{job_id}.json"


def save(job: Job) -> None:
    job.updated_at = datetime.now(timezone.utc)
    _path(job.id).write_text(job.model_dump_json(indent=2), encoding="utf-8")


def load(job_id: str) -> Optional[Job]:
    p = _path(job_id)
    if not p.exists():
        return None
    return Job.model_validate_json(p.read_text(encoding="utf-8"))


def list_all() -> list[Job]:
    jobs = []
    for p in sorted(settings.jobs_dir.glob("*.json"), reverse=True):
        try:
            jobs.append(Job.model_validate_json(p.read_text(encoding="utf-8")))
        except Exception:
            pass
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
