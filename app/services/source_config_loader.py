"""Load source configs from data/source_configs/sources.json.

Credentials are resolved server-side via env vars and are never returned
to the frontend. The safe_source() helper strips all credential references.
"""

from __future__ import annotations

import json
from typing import Optional

from app.config import settings

_SAFE_FIELDS = {"id", "label", "type", "enabled", "remote_path", "filename_patterns"}


def load_sources() -> list[dict]:
    path = settings.source_configs_dir / "sources.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def get_source_by_id(source_id: str) -> Optional[dict]:
    for s in load_sources():
        if s.get("id") == source_id:
            return s
    return None


def safe_source(s: dict) -> dict:
    """Strip credential references before returning to the frontend."""
    return {k: v for k, v in s.items() if k in _SAFE_FIELDS}
