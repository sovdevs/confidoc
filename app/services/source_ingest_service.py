"""Orchestrate pulling files from a configured source into Zone 1."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional

from app.config import settings
from app.connectors.base import RemoteFile
from app.services.ingest_registry import IngestRegistry
from app.storage import audit_log
from app.storage import jobs as job_store
from app.storage.jobs import Job, JobStatus


def _sanitize(name: str) -> str:
    """Prevent path traversal; strip unsafe characters."""
    name = Path(name).name          # drop any directory components
    name = re.sub(r"[^\w.\-]", "_", name)
    return name[:240]


def _file_status(
    registry: IngestRegistry,
    source_id: str,
    f: RemoteFile,
) -> tuple[str, Optional[str]]:
    """Return (status, previous_job_id)."""
    if not f.supported:
        return "unsupported", None
    status = registry.check_status(source_id, f.remote_path, f.size_bytes, f.modified_at)
    prev = registry.get_job_id(source_id, f.remote_path) if status == "changed" else None
    return status, prev


def annotate_file_list(source_id: str, files: list[RemoteFile]) -> list[RemoteFile]:
    """Fill in status (new/seen/changed/unsupported) from the ingest registry."""
    registry = IngestRegistry()
    for f in files:
        f.status, _ = _file_status(registry, source_id, f)
    return files


def pull_files(
    source_config: dict,
    remote_files: list[RemoteFile],
    *,
    src_lang: str = "de-DE",
    tgt_lang: str = "en-GB",
) -> dict:
    """Download selected files, create imported jobs, update the registry."""
    from app.connectors import get_connector

    source_id   = source_config["id"]
    source_type = source_config["type"]
    connector   = get_connector(source_config)
    registry    = IngestRegistry()

    imported: list[dict] = []
    skipped_seen  = 0
    unsupported   = 0
    errors: list[dict] = []

    for f in remote_files:
        if not f.supported:
            unsupported += 1
            continue

        status, prev_job_id = _file_status(registry, source_id, f)
        if status == "seen":
            skipped_seen += 1
            continue

        safe_name = _sanitize(f.filename)
        dest = settings.input_dir / safe_name
        settings.input_dir.mkdir(parents=True, exist_ok=True)

        result = connector.download_file(f.remote_path, dest)
        if not result.ok:
            errors.append({"remote_path": f.remote_path, "error": result.error})
            continue

        job = Job(
            filename=safe_name,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            status=JobStatus.imported,
            ingest_source_id=source_id,
            ingest_source_type=source_type,
            ingest_remote_path=f.remote_path,
            requires_ocr=f.requires_ocr,
        )
        job_store.save(job)

        # Audit: hash the remote path so filenames (potentially PHI) aren't logged
        path_hash = hashlib.sha256(f.remote_path.encode()).hexdigest()[:16]
        audit_log.log(job.id, "SOURCE_PULL_FILE", {
            "source_id":        source_id,
            "source_type":      source_type,
            "remote_path_hash": path_hash,
            "filename":         safe_name,
            "size_bytes":       result.size_bytes,
            "requires_ocr":     f.requires_ocr,
        })

        registry.register(
            source_id=source_id,
            remote_path=f.remote_path,
            size_bytes=f.size_bytes,
            modified_at=f.modified_at,
            job_id=job.id,
            content_sha256=result.content_sha256,
            previous_job_id=prev_job_id,
        )

        imported.append({
            "job_id":        job.id,
            "filename":      safe_name,
            "source_format": f.extension.lstrip("."),
            "requires_ocr":  f.requires_ocr,
        })

    return {
        "source_id":    source_id,
        "imported":     imported,
        "skipped_seen": skipped_seen,
        "unsupported":  unsupported,
        "errors":       errors,
    }
