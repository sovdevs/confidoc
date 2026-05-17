"""Ingest registry — tracks which remote files have already been pulled.

Stored as an append-only JSONL at data/zone1/ingest_registry.jsonl.
Credentials and PHI are never written here; only structural metadata.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from app.config import settings


class IngestRegistry:

    def _load(self) -> list[dict]:
        path = settings.ingest_registry_path
        if not path.exists():
            return []
        records: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass
        return records

    def _append(self, record: dict) -> None:
        path = settings.ingest_registry_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def check_status(
        self,
        source_id: str,
        remote_path: str,
        size_bytes: int,
        modified_at: str,
    ) -> str:
        """Return 'new', 'seen', or 'changed'."""
        for r in self._load():
            if r.get("source_id") != source_id or r.get("remote_path") != remote_path:
                continue
            if r.get("remote_size_bytes") == size_bytes and r.get("remote_modified_at") == modified_at:
                return "seen"
            return "changed"
        return "new"

    def get_job_id(self, source_id: str, remote_path: str) -> Optional[str]:
        """Return the most recent job_id for a (source, path) pair."""
        last: Optional[str] = None
        for r in self._load():
            if r.get("source_id") == source_id and r.get("remote_path") == remote_path:
                last = r.get("job_id")
        return last

    def register(
        self,
        *,
        source_id: str,
        remote_path: str,
        size_bytes: int,
        modified_at: str,
        job_id: str,
        content_sha256: str = "",
        previous_job_id: Optional[str] = None,
    ) -> None:
        record: dict = {
            "source_id": source_id,
            "remote_path": remote_path,
            "remote_size_bytes": size_bytes,
            "remote_modified_at": modified_at,
            "content_sha256": content_sha256,
            "job_id": job_id,
            "imported_at": datetime.now(timezone.utc).isoformat(),
        }
        if previous_job_id:
            record["previous_job_id"] = previous_job_id
            record["change_detected"] = True
        self._append(record)
