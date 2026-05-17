"""Local folder gateway — scan incoming/, run pipeline, write exports/.

Reuses the existing Confidoc ingest, anonymisation, and export pipeline.
No separate pipeline is built here; this is purely an intake/routing layer.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.config import settings
from app.connectors.base import SUPPORTED_EXTENSIONS, extension_info
from app.storage import audit_log
from app.storage import jobs as job_store
from app.storage.jobs import Job, JobStatus


class LocalGateway:

    def __init__(self) -> None:
        base             = settings.gateway_local_dir
        self.incoming    = base / "incoming"
        self.processing  = base / "processing"
        self.processed   = base / "processed"
        self.failed      = base / "failed"
        self.exports     = base / "exports"
        self.registry    = base / "registry.jsonl"

    def ensure_dirs(self) -> None:
        for d in (self.incoming, self.processing, self.processed, self.failed, self.exports):
            d.mkdir(parents=True, exist_ok=True)

    # ── Registry ──────────────────────────────────────────────────────────────

    def _log(self, event: str, filename: str,
             job_id: Optional[str] = None, **extra) -> None:
        record: dict = {
            "event":     event,
            "filename":  filename,
            "job_id":    job_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        record.update(extra)
        self.registry.parent.mkdir(parents=True, exist_ok=True)
        with open(self.registry, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def load_registry(self, limit: int = 50) -> list[dict]:
        if not self.registry.exists():
            return []
        lines = self.registry.read_text(encoding="utf-8").splitlines()
        records: list[dict] = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                pass
            if len(records) >= limit:
                break
        return records

    # ── Counts ────────────────────────────────────────────────────────────────

    def counts(self) -> dict:
        self.ensure_dirs()

        def _count(d: Path) -> int:
            return sum(1 for p in d.iterdir() if p.is_file() and not p.name.startswith("."))

        return {
            "incoming":   _count(self.incoming),
            "processing": _count(self.processing),
            "processed":  _count(self.processed),
            "failed":     _count(self.failed),
        }

    # ── Scan ──────────────────────────────────────────────────────────────────

    def scan_incoming(self) -> list[dict]:
        """List supported files in incoming/. Logs a 'detected' event per file."""
        self.ensure_dirs()
        results: list[dict] = []
        for p in sorted(self.incoming.iterdir()):
            if not p.is_file() or p.name.startswith("."):
                continue
            ext = p.suffix.lower()
            supported, requires_ocr, _ = extension_info(ext)
            results.append({
                "filename":    p.name,
                "size_bytes":  p.stat().st_size,
                "extension":   ext,
                "supported":   supported,
                "requires_ocr": requires_ocr,
            })
            if supported:
                self._log("detected", p.name)
        return results

    # ── File movement ─────────────────────────────────────────────────────────

    def _move_to_processing(self, filename: str) -> Path:
        src = self.incoming / filename
        dst = self.processing / filename
        shutil.move(str(src), dst)
        return dst

    def _move_to_processed(self, filename: str, job_id: str) -> None:
        src = self.processing / filename
        if src.exists():
            shutil.move(str(src), self.processed / f"{job_id}_{filename}")

    def _move_to_failed(self, filename: str) -> None:
        ts  = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        dst = self.failed / f"{ts}_{filename}"
        for candidate in (self.processing / filename, self.incoming / filename):
            if candidate.exists():
                shutil.move(str(candidate), dst)
                break

    # ── Pipeline ──────────────────────────────────────────────────────────────

    async def process_file(self, filename: str) -> dict:
        """Run the full gateway pipeline for one file from incoming/.

        Reuses the existing Confidoc pipeline functions directly.
        Auto-approve mode is controlled by settings.auto_approve_gateway_jobs.
        """
        from app.pipeline import anon, anon_llm, export, ingest
        from app.storage.jobs import Entity

        src = self.incoming / filename
        if not src.exists():
            return {"ok": False, "error": f"'{filename}' not found in incoming/"}

        ext = src.suffix.lower()
        supported, requires_ocr, _ = extension_info(ext)

        if not supported:
            self._log("failed", filename, error="unsupported file type")
            return {"ok": False, "error": f"Unsupported file type: {ext}"}

        if not requires_ocr:
            self._log("failed", filename, error="non-PDF extraction not yet supported")
            return {
                "ok": False,
                "error": "Non-PDF extraction is not yet supported. "
                         "Import the file manually and process it after extraction support is added.",
            }

        # ── Move to processing/ and read bytes ────────────────────────────────
        proc_path  = self._move_to_processing(filename)
        file_bytes = proc_path.read_bytes()

        job = Job(
            filename=filename,
            ingest_source_id="gateway_local",
            ingest_source_type="local_folder",
            requires_ocr=True,
        )
        job_store.save(job)
        self._log("imported", filename, job_id=job.id)
        audit_log.log(job.id, "GATEWAY_IMPORTED", {"filename": filename})

        auto = settings.auto_approve_gateway_jobs

        try:
            # ── OCR + entity detection (identical to normal upload pipeline) ──
            job = await ingest.run(job, file_bytes)
            job = anon.run(job)
            job = await anon_llm.run(job)
            self._log("processing_started", filename, job_id=job.id)

            if auto:
                return await self._auto_approve_and_export(job, filename)
            else:
                # Leave job in reviewing status for the human review UI
                job_store.update_status(job.id, JobStatus.reviewing)
                self._log("waiting_for_review", filename, job_id=job.id)
                audit_log.log(job.id, "GATEWAY_AWAITING_REVIEW", {"filename": filename})
                return {
                    "ok":     True,
                    "job_id": job.id,
                    "status": "waiting_for_review",
                    "mode":   "manual",
                }

        except Exception as exc:
            err = str(exc)[:300]
            job_store.update_status(job.id, JobStatus.failed, error=err)
            self._log("failed", filename, job_id=job.id, error=err)
            self._move_to_failed(filename)
            audit_log.log(job.id, "GATEWAY_FAILED", {"filename": filename, "error": err})
            return {"ok": False, "error": err, "job_id": job.id}

    async def _auto_approve_and_export(self, job: Job, filename: str) -> dict:
        """Auto-approve entities, run export pipeline, copy artifacts to exports/."""
        from app.pipeline import export
        from app.storage.jobs import Entity

        # Auto-approve all detected entities
        approved = [
            {**Entity.model_validate(e).model_dump(), "approved": True}
            for e in job.entities
        ]
        job_store.update_status(job.id, JobStatus.approved, entities=approved)
        audit_log.log(job.id, "GATEWAY_AUTO_APPROVED", {"count": len(approved)})

        # Generate reviewed markdown + token mapping + TMX/CSV
        job = job_store.load(job.id)
        job = export.run(job, reviewer="gateway_auto")

        # Copy Zone-2-safe export artifacts to gateway exports dir
        export_dir   = self.exports / job.id
        export_dir.mkdir(parents=True, exist_ok=True)
        copied: list[str] = []

        for attr in ("reviewed_md", "exported_tmx", "exported_csv"):
            rel = getattr(job, attr, None)
            if rel:
                src = settings.jobs_dir.parent / rel
                if src.exists():
                    dst = export_dir / src.name
                    shutil.copy2(src, dst)
                    copied.append(src.name)

        job_store.update_status(job.id, JobStatus.done)
        self._log("exported",  filename, job_id=job.id,
                  export_dir=str(export_dir), files=copied)
        self._log("completed", filename, job_id=job.id)
        self._move_to_processed(filename, job.id)
        audit_log.log(job.id, "GATEWAY_COMPLETED", {
            "filename": filename, "export_dir": str(export_dir), "files": copied,
        })

        return {
            "ok":            True,
            "job_id":        job.id,
            "status":        "completed",
            "mode":          "auto",
            "export_dir":    str(export_dir),
            "exported_files": copied,
        }
