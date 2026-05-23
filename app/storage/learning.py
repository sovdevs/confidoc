"""Encrypted per-job entity learning store.

Positive examples (approved entities) and negative suppressions (ignored spans)
are stored encrypted at rest using the same Fernet key as token maps.

Zone 1 data — never included in Zone 2 exports or plaintext files.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet

from app.storage.mappings import _get_key


def _path(learning_dir: Path, job_id: str) -> Path:
    return learning_dir / f"{job_id}.enc"


def _load(learning_dir: Path, job_id: str) -> dict:
    p = _path(learning_dir, job_id)
    if not p.exists():
        return {"positive": [], "negative": []}
    try:
        data = json.loads(Fernet(_get_key()).decrypt(p.read_bytes()).decode())
        return {"positive": data.get("positive", []), "negative": data.get("negative", [])}
    except Exception:
        return {"positive": [], "negative": []}


def _save(learning_dir: Path, job_id: str, data: dict) -> None:
    learning_dir.mkdir(parents=True, exist_ok=True)
    p = _path(learning_dir, job_id)
    p.write_bytes(Fernet(_get_key()).encrypt(
        json.dumps(data, ensure_ascii=False).encode()
    ))
    p.chmod(0o600)


def add_positive(
    learning_dir: Path,
    job_id: str,
    entity_id: str,
    text: str,
    label: str,
    replacement: str = "",
    context_before: str = "",
    context_after: str = "",
) -> None:
    """Record an approved entity as a positive learning example."""
    data = _load(learning_dir, job_id)
    # Remove any previous entry for this entity (user may have changed state)
    data["positive"] = [e for e in data["positive"] if e.get("id") != entity_id]
    data["negative"] = [e for e in data["negative"] if e.get("id") != entity_id]
    data["positive"].append({
        "id":             entity_id,
        "text":           text,
        "label":          label,
        "replacement":    replacement,
        "context_before": context_before,
        "context_after":  context_after,
        "approved_at":    datetime.now(timezone.utc).isoformat(),
    })
    _save(learning_dir, job_id, data)


def add_negative(
    learning_dir: Path,
    job_id: str,
    entity_id: str,
    text: str,
    label: str,
) -> None:
    """Record an ignored entity as a negative learning example (suppress in re-detect)."""
    data = _load(learning_dir, job_id)
    data["positive"] = [e for e in data["positive"] if e.get("id") != entity_id]
    data["negative"] = [e for e in data["negative"] if e.get("id") != entity_id]
    data["negative"].append({
        "id":         entity_id,
        "text":       text,
        "label":      label,
        "ignored_at": datetime.now(timezone.utc).isoformat(),
    })
    _save(learning_dir, job_id, data)


def remove(learning_dir: Path, job_id: str, entity_id: str) -> None:
    """Remove entity from both stores (Delete action — no learning effect)."""
    data = _load(learning_dir, job_id)
    data["positive"] = [e for e in data["positive"] if e.get("id") != entity_id]
    data["negative"] = [e for e in data["negative"] if e.get("id") != entity_id]
    _save(learning_dir, job_id, data)


def get_ignored_set(learning_dir: Path, job_id: str) -> set[tuple[str, str]]:
    """Return {(text_lower, label)} for all ignored entities in this job."""
    return {
        (e["text"].strip().lower(), e["label"])
        for e in _load(learning_dir, job_id)["negative"]
    }


def counts(learning_dir: Path, job_id: str) -> dict[str, int]:
    data = _load(learning_dir, job_id)
    return {"positive": len(data["positive"]), "negative": len(data["negative"])}
