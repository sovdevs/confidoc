"""Server-side session store — persisted to disk so deploys don't log everyone out.

Tokens are 32 random bytes (URL-safe base64). session_keys (ephemeral BYOK API
keys) are intentionally NOT persisted — they live only in memory for the
process lifetime.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.config import settings

COOKIE_NAME = "confidoc_session"

# In-memory cache — source of truth is the JSON file on disk
_sessions: dict[str, dict] = {}
_session_keys: dict[str, dict] = {}   # token → {provider: key} — never persisted


def _sessions_path() -> Path:
    return settings.auth_dir / "sessions.json"


def _load() -> None:
    p = _sessions_path()
    if not p.exists():
        return
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        now = datetime.now(timezone.utc)
        for token, s in raw.items():
            expires = datetime.fromisoformat(s["expires_at"])
            if expires > now:
                _sessions[token] = {"username": s["username"], "expires_at": expires}
    except Exception:
        pass


def _save() -> None:
    try:
        settings.auth_dir.mkdir(parents=True, exist_ok=True)
        data = {
            t: {"username": s["username"], "expires_at": s["expires_at"].isoformat()}
            for t, s in _sessions.items()
        }
        _sessions_path().write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


# Load persisted sessions on module import
_load()


def create_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours)
    _sessions[token] = {"username": username, "expires_at": expires}
    _expire_old()
    _save()
    return token


def get_session(token: str) -> Optional[dict]:
    s = _sessions.get(token)
    if not s:
        return None
    if datetime.now(timezone.utc) > s["expires_at"]:
        _sessions.pop(token, None)
        _save()
        return None
    return s


def get_username(token: str) -> Optional[str]:
    s = get_session(token)
    return s["username"] if s else None


def destroy_session(token: str) -> None:
    _sessions.pop(token, None)
    _session_keys.pop(token, None)
    _save()


def set_session_key(token: str, provider: str, api_key: str) -> None:
    """Ephemeral BYOK key — memory only, never written to disk."""
    _session_keys.setdefault(token, {})[provider] = api_key


def get_session_key(token: str, provider: str) -> Optional[str]:
    s = get_session(token)
    return _session_keys.get(token, {}).get(provider) if s else None


def _expire_old() -> None:
    now = datetime.now(timezone.utc)
    stale = [t for t, s in _sessions.items() if now > s["expires_at"]]
    for t in stale:
        _sessions.pop(t, None)
        _session_keys.pop(t, None)
