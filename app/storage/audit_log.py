"""Append-only JSONL audit log. One line per event."""

import json
from datetime import datetime, timezone

from app.config import settings


def log(job_id: str, event: str, detail: dict | None = None, actor: str = "system") -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "job_id": job_id,
        "actor": actor,
        "event": event,
        "detail": detail or {},
    }
    with settings.audit_log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read(job_id: str | None = None) -> list[dict]:
    if not settings.audit_log.exists():
        return []
    entries = []
    for line in settings.audit_log.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
            if job_id is None or e.get("job_id") == job_id:
                entries.append(e)
        except Exception:
            pass
    return entries
