"""Encrypted per-user settings — stored in data/auth/user_settings/{username}.enc.

Content is Fernet-encrypted JSON. The key comes from SETTINGS_KEY (or MAPPING_KEY
as fallback). Secrets (API keys, SFTP passwords, private keys) are stored here —
never in sources.json or plaintext config files.

Schema (the decrypted JSON):
{
  "ocr_provider": "openrouter",
  "ocr_model": "google/gemini-2.0-flash",
  "anon_provider": "openrouter",
  "anon_model": "google/gemini-2.0-flash",
  "export_provider": "openrouter",
  "export_model": "google/gemini-2.0-flash",
  "api_keys": {                        # provider -> key (remembered)
    "openrouter": "sk-or-...",
    "google": "AIza..."
  },
  "sftp_sources": [                    # user-managed SFTP gateway sources
    {
      "id": "my_sftp",
      "label": "My clinic server",
      "host": "1.2.3.4",
      "port": 22,
      "username": "confidoc",
      "gateway_base": "/home/confidoc/gateway",
      "filename_patterns": ["*.pdf"],
      "auth_method": "key",            # "key" | "password"
      "private_key": "-----BEGIN...",  # PEM content — stored here, not sources.json
      "password": null,
      "enabled": true
    }
  ]
}

When returning data to the frontend, call safe_settings() to redact secrets.
"""

from __future__ import annotations

import json
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

_SECRET_FIELDS = {"private_key", "password", "passphrase", "api_key", "token"}


def _get_key() -> bytes:
    # Delegate to mappings._get_key() which handles auto-generation for dev
    # and uses MAPPING_KEY / SETTINGS_KEY for production.
    from app.storage.mappings import _get_key as _mapping_key
    return _mapping_key()


def _path(username: str):
    return settings.user_settings_dir / f"{username}.enc"


def load(username: str) -> dict:
    p = _path(username)
    if not p.exists():
        return _defaults()
    try:
        f = Fernet(_get_key())
        return json.loads(f.decrypt(p.read_bytes()).decode("utf-8"))
    except (InvalidToken, Exception):
        return _defaults()


def save(username: str, data: dict) -> None:
    settings.user_settings_dir.mkdir(parents=True, exist_ok=True)
    f = Fernet(_get_key())
    _path(username).write_bytes(
        f.encrypt(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    )


def update(username: str, patch: dict) -> dict:
    data = load(username)
    data.update(patch)
    save(username, data)
    return data


def safe_settings(data: dict) -> dict:
    """Return settings with secrets redacted — safe to send to frontend."""
    result = dict(data)

    # Redact top-level api_keys: show only which providers are configured
    if "api_keys" in result:
        result["api_keys"] = {
            provider: "configured" if key else None
            for provider, key in result["api_keys"].items()
        }

    # Redact secrets inside sftp_sources
    safe_sources = []
    for src in result.get("sftp_sources", []):
        safe_src = {k: v for k, v in src.items() if k not in _SECRET_FIELDS}
        for field in _SECRET_FIELDS:
            if field in src:
                safe_src[field] = "configured" if src[field] else None
        safe_sources.append(safe_src)
    result["sftp_sources"] = safe_sources

    return result


def _defaults() -> dict:
    return {
        "ocr_provider":    settings.pdf_provider,
        "ocr_model":       settings.pdf_model,
        "anon_provider":   settings.anon_provider,
        "anon_model":      settings.anon_model,
        "export_provider": settings.anon_provider,
        "export_model":    settings.anon_model,
        "api_keys":        {},
        "sftp_sources":    [],
    }


def get_sftp_sources(username: str) -> list[dict]:
    """Return all SFTP sources for this user (with secrets intact — server-side only)."""
    return load(username).get("sftp_sources", [])


def upsert_sftp_source(username: str, source: dict) -> None:
    """Add or replace an SFTP source by id."""
    data = load(username)
    sources = data.get("sftp_sources", [])
    sources = [s for s in sources if s.get("id") != source.get("id")]
    sources.append(source)
    data["sftp_sources"] = sources
    save(username, data)


def delete_sftp_source(username: str, source_id: str) -> None:
    data = load(username)
    data["sftp_sources"] = [
        s for s in data.get("sftp_sources", []) if s.get("id") != source_id
    ]
    save(username, data)
