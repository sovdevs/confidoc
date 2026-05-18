"""Server-side session store.

Sessions are kept in memory (dict). A restart logs everyone out — acceptable
for demo-grade auth. Session tokens are 32 random bytes (URL-safe base64),
unguessable without signing overhead.

TODO (production hardening): persist sessions to disk or Redis; add IP binding.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.config import settings

COOKIE_NAME = "confidoc_session"

# {token: {"username": str, "expires_at": datetime, "session_keys": dict}}
_sessions: dict[str, dict] = {}


def create_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours)
    _sessions[token] = {
        "username":    username,
        "expires_at":  expires,
        "session_keys": {},  # ephemeral BYOK keys — never persisted
    }
    _expire_old()
    return token


def get_session(token: str) -> Optional[dict]:
    s = _sessions.get(token)
    if not s:
        return None
    if datetime.now(timezone.utc) > s["expires_at"]:
        _sessions.pop(token, None)
        return None
    return s


def get_username(token: str) -> Optional[str]:
    s = get_session(token)
    return s["username"] if s else None


def destroy_session(token: str) -> None:
    _sessions.pop(token, None)


def set_session_key(token: str, provider: str, api_key: str) -> None:
    """Store an ephemeral BYOK key for this session only — never written to disk."""
    s = _sessions.get(token)
    if s:
        s["session_keys"][provider] = api_key


def get_session_key(token: str, provider: str) -> Optional[str]:
    s = get_session(token)
    return s["session_keys"].get(provider) if s else None


def _expire_old() -> None:
    now = datetime.now(timezone.utc)
    stale = [t for t, s in _sessions.items() if now > s["expires_at"]]
    for t in stale:
        _sessions.pop(t, None)
