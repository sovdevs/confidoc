"""SFTP Secure Gateway — mirrors the local folder gateway lifecycle over SSH.

Remote folder structure (all under gateway_base in the source config):
  incoming/    source files to pull
  processing/  moved there on pickup
  processed/   {job_id}_{filename} on success
  failed/      {timestamp}_{filename} on error
  exports/     {job_id}/ on auto-approve

Local tracking (data/gateway/sftp/{source_id}/):
  registry.jsonl    append-only event log
  batch_status.json live progress for the current Process All batch

Credentials are resolved from env vars at connect time — never stored locally.
"""

from __future__ import annotations

import io
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.config import settings
from app.connectors.base import extension_info
from app.storage import audit_log
from app.storage import jobs as job_store
from app.storage.jobs import Job, JobStatus


def _env(cfg: dict, key: str) -> Optional[str]:
    import os
    env_name = cfg.get(key)
    return os.environ.get(env_name) if env_name else None


def _safe_err(exc: Exception) -> str:
    msg = str(exc)
    if any(w in msg.lower() for w in ("password", "private key", "authentication")):
        return "Authentication failed"
    return msg[:300]


class SFTPGateway:

    def __init__(self, source_config: dict) -> None:
        self.config    = source_config
        self.source_id = source_config["id"]

        base = source_config.get("gateway_base", "").rstrip("/")
        if not base:
            raise ValueError(
                f"Source '{self.source_id}' must have a 'gateway_base' field for SFTP gateway use"
            )
        self.r_base       = base
        self.r_incoming   = f"{base}/incoming"
        self.r_processing = f"{base}/processing"
        self.r_processed  = f"{base}/processed"
        self.r_failed     = f"{base}/failed"
        self.r_exports    = f"{base}/exports"

        local              = settings.gateway_sftp_dir / self.source_id
        self.local_dir     = local
        self.registry      = local / "registry.jsonl"
        self.batch_file    = local / "batch_status.json"

    # ── Connection ────────────────────────────────────────────────────────────

    def _connect(self):
        """Return (SSHClient, SFTPClient). Caller is responsible for closing both."""
        try:
            import paramiko
        except ImportError:
            raise RuntimeError("paramiko is required for SFTP gateway. pip install paramiko")

        cfg      = self.config
        host     = cfg["host"]
        port     = int(cfg.get("port", 22))
        username = _env(cfg, "username_env") or cfg.get("username", "")

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        kwargs: dict = dict(hostname=host, port=port, username=username, timeout=20)

        # Inline PEM key via env var (for cloud deployments without disk access)
        key_content = _env(cfg, "private_key_content_env")
        key_path    = _env(cfg, "private_key_path_env") or cfg.get("private_key_path")
        password    = _env(cfg, "password_env")

        if key_content:
            pkey = paramiko.RSAKey.from_private_key(io.StringIO(key_content))
            kwargs["pkey"] = pkey
        elif key_path:
            kwargs["key_filename"] = key_path
        elif password:
            kwargs["password"] = password

        client.connect(**kwargs)
        return client, client.open_sftp()

    # ── Remote helpers ────────────────────────────────────────────────────────

    def _mkdir(self, sftp, path: str) -> None:
        try:
            sftp.mkdir(path)
        except OSError:
            pass  # already exists

    def _move(self, sftp, src: str, dst: str) -> None:
        sftp.rename(src, dst)

    def ensure_remote_dirs(self) -> dict:
        """Create the gateway folder structure on the remote server."""
        client, sftp = self._connect()
        try:
            created = []
            for d in (self.r_incoming, self.r_processing,
                      self.r_processed, self.r_failed, self.r_exports):
                try:
                    sftp.stat(d)
                except FileNotFoundError:
                    sftp.mkdir(d)
                    created.append(d)
            return {"ok": True, "created": created}
        except Exception as exc:
            return {"ok": False, "error": _safe_err(exc)}
        finally:
            sftp.close(); client.close()

    def test_connection(self) -> dict:
        try:
            client, sftp = self._connect()
            sftp.stat(self.r_base)
            sftp.close(); client.close()
            return {"ok": True, "message": f"Connected. Base path '{self.r_base}' is accessible."}
        except Exception as exc:
            return {"ok": False, "message": f"Connection failed: {_safe_err(exc)}"}

    def remote_counts(self) -> dict:
        """Count files in each gateway folder on the remote server."""
        client, sftp = self._connect()
        try:
            def _count(path: str) -> int:
                try:
                    return sum(1 for a in sftp.listdir_attr(path)
                               if not a.filename.startswith("."))
                except Exception:
                    return -1  # -1 = folder missing

            return {
                "incoming":   _count(self.r_incoming),
                "processing": _count(self.r_processing),
                "processed":  _count(self.r_processed),
                "failed":     _count(self.r_failed),
            }
        finally:
            sftp.close(); client.close()

    # ── Registry (local) ──────────────────────────────────────────────────────

    def _log(self, event: str, filename: str,
             job_id: Optional[str] = None, **extra) -> None:
        record: dict = {
            "event":     event,
            "filename":  filename,
            "job_id":    job_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        record.update(extra)
        self.local_dir.mkdir(parents=True, exist_ok=True)
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

    # ── Batch tracking (local) ────────────────────────────────────────────────

    def batch_start(self, filenames: list[str]) -> str:
        batch_id = uuid.uuid4().hex[:8]
        status = {
            "batch_id":    batch_id,
            "started_at":  datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "total":       len(filenames),
            "processed":   0,
            "succeeded":   0,
            "failed":      0,
            "running":     True,
            "results":     [],
        }
        self.local_dir.mkdir(parents=True, exist_ok=True)
        self.batch_file.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
        return batch_id

    def batch_update(self, result: dict) -> None:
        if not self.batch_file.exists():
            return
        status = json.loads(self.batch_file.read_text(encoding="utf-8"))
        status["processed"] += 1
        status["succeeded" if result.get("ok") else "failed"] += 1
        status["results"].append(result)
        self.batch_file.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")

    def batch_finish(self) -> None:
        if not self.batch_file.exists():
            return
        status = json.loads(self.batch_file.read_text(encoding="utf-8"))
        status["running"]     = False
        status["finished_at"] = datetime.now(timezone.utc).isoformat()
        self.batch_file.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")

    def batch_status(self) -> Optional[dict]:
        if not self.batch_file.exists():
            return None
        try:
            return json.loads(self.batch_file.read_text(encoding="utf-8"))
        except Exception:
            return None

    # ── Export push ───────────────────────────────────────────────────────────

    def push_job_exports(self, job_id: str) -> dict:
        """Upload all available export artifacts for a job to remote exports/{job_id}/.

        Collects: reviewed_md, normalized_md, all policy packages, and LLM runs.
        Safe to call multiple times — re-uploads the latest versions.
        """
        job = job_store.load(job_id)
        if not job:
            return {"ok": False, "error": "Job not found"}

        files_to_push: list[Path] = []

        # Pseudonymized / normalized markdown
        for attr in ("reviewed_md", "normalized_md"):
            rel = getattr(job, attr, None)
            if rel:
                p = settings.jobs_dir.parent / rel
                if p.exists():
                    files_to_push.append(p)

        # Policy engine packages
        pkg_base = settings.prepared_packages_dir
        if pkg_base.exists():
            for pkg_dir in pkg_base.iterdir():
                if not pkg_dir.is_dir():
                    continue
                manifest = pkg_dir / "manifest.json"
                if not manifest.exists():
                    continue
                try:
                    meta = json.loads(manifest.read_text(encoding="utf-8"))
                    if meta.get("job_id") != job_id:
                        continue
                except Exception:
                    continue
                # Push Zone-2-safe files only — never token_map.enc
                for name in ("prepared.md", "manifest.json", "risk_report.json",
                              "usefulness_report.json", "transformation_log.json"):
                    f = pkg_dir / name
                    if f.exists():
                        files_to_push.append(f)
                # Supplementary exports (CSV, TMX, DOCX, XLIFF)
                for f in pkg_dir.glob(f"{Path(job.filename).stem}.*"):
                    if f.suffix.lower() not in (".enc", ".zip"):
                        files_to_push.append(f)

        # LLM export runs
        llm_dir = settings.llm_runs_dir / job_id
        if llm_dir.exists():
            for f in llm_dir.glob("*.json"):
                files_to_push.append(f)

        if not files_to_push:
            return {"ok": True, "pushed": 0, "message": "No export artifacts found yet"}

        remote_dir = f"{self.r_exports}/{job_id}"
        pushed: list[str] = []
        errors: list[str] = []

        try:
            client, sftp = self._connect()
            self._mkdir(sftp, self.r_exports)
            self._mkdir(sftp, remote_dir)
            for local_file in files_to_push:
                try:
                    sftp.put(str(local_file), f"{remote_dir}/{local_file.name}")
                    pushed.append(local_file.name)
                except Exception as exc:
                    errors.append(f"{local_file.name}: {_safe_err(exc)}")
            sftp.close(); client.close()
        except Exception as exc:
            return {"ok": False, "error": f"Connection failed: {_safe_err(exc)}"}

        self._log("exports_pushed", job.filename, job_id=job_id,
                  remote_dir=remote_dir, pushed=pushed, errors=errors)

        return {
            "ok":        True,
            "job_id":    job_id,
            "remote_dir": remote_dir,
            "pushed":    pushed,
            "errors":    errors,
        }

    # ── Scan ──────────────────────────────────────────────────────────────────

    def scan_incoming(self) -> list[dict]:
        """List supported files in remote incoming/."""
        patterns = self.config.get("filename_patterns", ["*.pdf"])
        import fnmatch

        client, sftp = self._connect()
        try:
            attrs = sftp.listdir_attr(self.r_incoming)
        except Exception as exc:
            raise RuntimeError(f"Cannot list remote incoming/: {_safe_err(exc)}")
        finally:
            sftp.close(); client.close()

        results = []
        for a in attrs:
            name = a.filename or ""
            if not name or name.startswith("."):
                continue
            if not any(fnmatch.fnmatch(name, p) for p in patterns):
                continue
            ext = Path(name).suffix.lower()
            supported, requires_ocr, _ = extension_info(ext)
            results.append({
                "filename":    name,
                "size_bytes":  a.st_size or 0,
                "extension":   ext,
                "supported":   supported,
                "requires_ocr": requires_ocr,
            })

        for f in results:
            if f["supported"]:
                self._log("detected", f["filename"])

        return results

    # ── Pipeline ──────────────────────────────────────────────────────────────

    async def process_file(self, filename: str, force_manual: bool = False) -> dict:
        """Full gateway pipeline for one remote file."""
        from app.pipeline import anon, anon_llm, ingest
        from app.storage.jobs import Entity

        ext = Path(filename).suffix.lower()
        _, requires_ocr, _ = extension_info(ext)

        if not requires_ocr:
            self._log("failed", filename, error="non-PDF extraction not yet supported")
            return {"ok": False, "error": "Non-PDF extraction not yet supported in Phase 1"}

        # ── Step 1: move remote incoming → processing ─────────────────────────
        try:
            client, sftp = self._connect()
            self._move(sftp, f"{self.r_incoming}/{filename}", f"{self.r_processing}/{filename}")
            sftp.close(); client.close()
        except Exception as exc:
            err = _safe_err(exc)
            self._log("failed", filename, error=f"remote move to processing failed: {err}")
            return {"ok": False, "error": err}

        # ── Step 2: download to local input_dir ───────────────────────────────
        local_path = settings.input_dir / filename
        settings.input_dir.mkdir(parents=True, exist_ok=True)
        try:
            client, sftp = self._connect()
            sftp.get(f"{self.r_processing}/{filename}", str(local_path))
            sftp.close(); client.close()
        except Exception as exc:
            err = _safe_err(exc)
            self._log("failed", filename, error=f"download failed: {err}")
            self._try_move_remote_to_failed(filename)
            return {"ok": False, "error": err}

        file_bytes = local_path.read_bytes()

        # ── Step 3: create job and run pipeline ───────────────────────────────
        job = Job(
            filename=filename,
            ingest_source_id=self.source_id,
            ingest_source_type="sftp_gateway",
            requires_ocr=True,
        )
        job_store.save(job)
        self._log("imported", filename, job_id=job.id)
        audit_log.log(job.id, "SFTP_GATEWAY_IMPORTED", {
            "source_id": self.source_id, "filename": filename,
        })

        auto = (not force_manual) and settings.auto_approve_gateway_jobs

        try:
            job = await ingest.run(job, file_bytes)
            job = anon.run(job)
            job = await anon_llm.run(job)
            self._log("processing_started", filename, job_id=job.id)

            if auto:
                result = await self._auto_approve_and_export(job, filename)
            else:
                job_store.update_status(job.id, JobStatus.reviewing)
                self._log("waiting_for_review", filename, job_id=job.id)
                audit_log.log(job.id, "SFTP_GATEWAY_AWAITING_REVIEW", {"filename": filename})
                result = {
                    "ok":     True,
                    "job_id": job.id,
                    "status": "waiting_for_review",
                    "mode":   "manual",
                }

            # Move remote processing → processed
            self._try_move_remote_to_processed(filename, job.id)
            return result

        except Exception as exc:
            err = str(exc)[:300]
            job_store.update_status(job.id, JobStatus.failed, error=err)
            self._log("failed", filename, job_id=job.id, error=err)
            audit_log.log(job.id, "SFTP_GATEWAY_FAILED", {"filename": filename, "error": err})
            self._try_move_remote_to_failed(filename)
            return {"ok": False, "error": err, "job_id": job.id}

    async def _auto_approve_and_export(self, job: Job, filename: str) -> dict:
        from app.pipeline import export
        from app.storage.jobs import Entity

        approved = [
            {**Entity.model_validate(e).model_dump(), "approved": True}
            for e in job.entities
        ]
        job_store.update_status(job.id, JobStatus.approved, entities=approved)
        audit_log.log(job.id, "SFTP_GATEWAY_AUTO_APPROVED", {"count": len(approved)})

        job = job_store.load(job.id)
        job = export.run(job, reviewer="sftp_gateway_auto")

        # Copy export artifacts locally and upload to remote exports/{job_id}/
        local_export_dir = settings.gateway_sftp_dir / self.source_id / "exports" / job.id
        local_export_dir.mkdir(parents=True, exist_ok=True)
        copied: list[str] = []

        for attr in ("reviewed_md", "exported_tmx", "exported_csv"):
            rel = getattr(job, attr, None)
            if rel:
                src = settings.jobs_dir.parent / rel
                if src.exists():
                    dst = local_export_dir / src.name
                    shutil.copy2(src, dst)
                    copied.append(src.name)

        remote_export = self._upload_exports(job.id, local_export_dir, copied)
        job_store.update_status(job.id, JobStatus.done)

        self._log("exported",  filename, job_id=job.id,
                  remote_export_dir=remote_export, files=copied)
        self._log("completed", filename, job_id=job.id)
        audit_log.log(job.id, "SFTP_GATEWAY_COMPLETED", {
            "filename": filename, "remote_export": remote_export, "files": copied,
        })

        return {
            "ok":                True,
            "job_id":            job.id,
            "status":            "completed",
            "mode":              "auto",
            "remote_export_dir": remote_export,
            "exported_files":    copied,
        }

    def _upload_exports(self, job_id: str, local_dir: Path, filenames: list[str]) -> str:
        """Upload export files to remote exports/{job_id}/. Returns remote path."""
        remote_job_dir = f"{self.r_exports}/{job_id}"
        try:
            client, sftp = self._connect()
            self._mkdir(sftp, remote_job_dir)
            for name in filenames:
                local_file = local_dir / name
                if local_file.exists():
                    sftp.put(str(local_file), f"{remote_job_dir}/{name}")
            sftp.close(); client.close()
        except Exception as exc:
            self._log("export_upload_failed", job_id,
                      error=_safe_err(exc), files=filenames)
        return remote_job_dir

    def _try_move_remote_to_processed(self, filename: str, job_id: str) -> None:
        try:
            ts  = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            client, sftp = self._connect()
            self._move(sftp,
                       f"{self.r_processing}/{filename}",
                       f"{self.r_processed}/{job_id}_{filename}")
            sftp.close(); client.close()
        except Exception:
            pass  # non-fatal — file may already have been moved

    def _try_move_remote_to_failed(self, filename: str) -> None:
        try:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            client, sftp = self._connect()
            # Try processing/ first, then incoming/
            for src in (f"{self.r_processing}/{filename}",
                        f"{self.r_incoming}/{filename}"):
                try:
                    sftp.stat(src)
                    self._move(sftp, src, f"{self.r_failed}/{ts}_{filename}")
                    break
                except FileNotFoundError:
                    continue
            sftp.close(); client.close()
        except Exception:
            pass
