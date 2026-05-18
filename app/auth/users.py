"""User store — predefined users only, no sign-up.

Users are stored in data/auth/users.json:
[{"username": "alice", "hashed_password": "$2b$...", "created_at": "..."}]

Passwords are hashed with bcrypt (via the bcrypt package directly).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import bcrypt

from app.config import settings


def _load() -> list[dict]:
    if not settings.users_file.exists():
        return []
    try:
        return json.loads(settings.users_file.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(users: list[dict]) -> None:
    settings.users_file.parent.mkdir(parents=True, exist_ok=True)
    settings.users_file.write_text(
        json.dumps(users, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def get_user(username: str) -> Optional[dict]:
    for u in _load():
        if u.get("username") == username:
            return u
    return None


def verify_password(username: str, password: str) -> bool:
    user = get_user(username)
    if not user:
        return False
    stored = user["hashed_password"]
    if isinstance(stored, str):
        stored = stored.encode("utf-8")
    return bcrypt.checkpw(password.encode("utf-8"), stored)


def create_user(username: str, password: str) -> None:
    """Create or update a user. Raises ValueError if username is invalid."""
    if not username or not username.isidentifier():
        raise ValueError("Username must be a valid identifier (letters, digits, underscores)")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")

    users = _load()
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    now = datetime.now(timezone.utc).isoformat()

    for u in users:
        if u["username"] == username:
            u["hashed_password"] = hashed
            u["updated_at"] = now
            _save(users)
            return

    users.append({"username": username, "hashed_password": hashed, "created_at": now})
    _save(users)


def list_users() -> list[str]:
    return [u["username"] for u in _load()]
