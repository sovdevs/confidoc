"""Stable pseudonymization token mapping.

Token strategy
──────────────
Identity labels (PATIENT_NAME, PHYSICIAN_NAME, ADDRESS, LOCATION, CASE_ID,
                 ID_NUMBER, PHONE):
    Same text → same token within a job.
    "Maria Schmidt" → [PATIENT_NAME_001] every time it appears.

DATE:
    Occurrence-based — each entity gets its own numbered token regardless of
    whether the date string repeats. "11.10.2023" appearing 6 times becomes
    [DATE_001] … [DATE_006] so that report date, birth date, admission date,
    and exam date remain independently traceable and reconstructable.

Dismissed entities receive no token and are not included in the mapping.

Encryption
──────────
Mapping files are encrypted with Fernet (AES-128-CBC + HMAC-SHA256).
The symmetric key is read from the MAPPING_KEY environment variable
(base64-urlsafe Fernet key, 44 characters).

If MAPPING_KEY is not set, a key is auto-generated and persisted to
data/mappings/.key (permissions 0o600) with a warning logged.
Production deployments must always set MAPPING_KEY explicitly.
"""

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet

from app.config import settings
from app.storage.jobs import Entity

logger = logging.getLogger(__name__)

# These labels get one token per occurrence (not per unique text value)
_OCCURRENCE_BASED = {"DATE"}


# ── Key management ────────────────────────────────────────────────────────────

def _get_key() -> bytes:
    raw = os.getenv("MAPPING_KEY", "").strip()
    if raw:
        return raw.encode()

    key_file = settings.mappings_dir / ".key"
    if key_file.exists():
        return key_file.read_bytes().strip()

    key = Fernet.generate_key()
    settings.mappings_dir.mkdir(parents=True, exist_ok=True)
    key_file.write_bytes(key)
    key_file.chmod(0o600)
    logger.warning(
        "MAPPING_KEY env var not set. Generated a dev key at %s. "
        "Set MAPPING_KEY in production.", key_file,
    )
    return key


def _fernet() -> Fernet:
    return Fernet(_get_key())


# ── Token assignment ──────────────────────────────────────────────────────────

def assign_tokens(entities: list[Entity]) -> tuple[list[Entity], dict[str, str]]:
    """Assign stable numbered tokens to all approved entities.

    Returns:
        updated   — entity list with ``replacement`` set to the stable token;
                    dismissed entities are returned unchanged with no token.
        mapping   — {stable_token: original_text} decryption dictionary.
    """
    counters: dict[str, int] = defaultdict(int)
    identity_index: dict[tuple[str, str], str] = {}  # (label, text) → token

    mapping: dict[str, str] = {}
    updated: list[Entity] = []

    for e in sorted(entities, key=lambda e: e.start):
        if not e.approved:
            # Dismissed or still-pending: original text is preserved in the output.
            # Set replacement = text so the stored entity is unambiguous — it will
            # not cause any substitution, and carries no generic label token.
            updated.append(e.model_copy(update={"replacement": e.text}))
            continue

        if e.label in _OCCURRENCE_BASED:
            # New token for every occurrence
            counters[e.label] += 1
            token = f"[{e.label}_{counters[e.label]:03d}]"
        else:
            key = (e.label, e.text.strip())
            if key not in identity_index:
                counters[e.label] += 1
                token = f"[{e.label}_{counters[e.label]:03d}]"
                identity_index[key] = token
            else:
                token = identity_index[key]

        mapping[token] = e.text
        updated.append(e.model_copy(update={"replacement": token}))

    return updated, mapping


# ── Persistence ───────────────────────────────────────────────────────────────

def save(job_id: str, mapping: dict[str, str], created_by: str = "system") -> Path:
    """Encrypt and write the token mapping for a job. Returns the file path."""
    settings.mappings_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({
        "job_id": job_id,
        "created_by": created_by,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tokens": mapping,
    }, ensure_ascii=False, indent=2).encode("utf-8")

    encrypted = _fernet().encrypt(payload)
    path = settings.mappings_dir / f"{job_id}.enc"
    path.write_bytes(encrypted)
    path.chmod(0o600)
    return path


def load(job_id: str) -> Optional[dict[str, str]]:
    """Decrypt and return the {token: original_text} mapping, or None."""
    path = settings.mappings_dir / f"{job_id}.enc"
    if not path.exists():
        return None
    try:
        payload = _fernet().decrypt(path.read_bytes())
        return json.loads(payload.decode("utf-8"))["tokens"]
    except Exception as exc:
        logger.error("Failed to decrypt mapping for job %s: %s", job_id, exc)
        return None


# ── Rehydration ───────────────────────────────────────────────────────────────

def rehydrate(text: str, mapping: dict[str, str]) -> str:
    """Replace every stable token in *text* with its original value."""
    for token, original in mapping.items():
        text = text.replace(token, original)
    return text
