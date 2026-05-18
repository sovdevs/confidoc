"""User settings API — LLM defaults, SFTP sources, API keys."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.middleware import current_user
from app.services import user_settings as us

router = APIRouter()


@router.get("/api/user/settings")
def get_settings(username: str = Depends(current_user)):
    return us.safe_settings(us.load(username))


class SettingsPatch(BaseModel):
    ocr_provider:    str | None = None
    ocr_model:       str | None = None
    anon_provider:   str | None = None
    anon_model:      str | None = None
    export_provider: str | None = None
    export_model:    str | None = None


@router.put("/api/user/settings")
def update_settings(body: SettingsPatch, username: str = Depends(current_user)):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    updated = us.update(username, patch)
    return us.safe_settings(updated)


class ApiKeyBody(BaseModel):
    provider: str
    api_key: str
    remember: bool = True


@router.post("/api/user/settings/api-key")
def save_api_key(body: ApiKeyBody, username: str = Depends(current_user)):
    """Save a provider API key. If remember=False, only the existence is acknowledged
    (caller should store it session-side via a separate mechanism)."""
    if body.remember:
        data = us.load(username)
        data.setdefault("api_keys", {})[body.provider] = body.api_key
        us.save(username, data)
    return {"ok": True, "provider": body.provider, "remembered": body.remember}


@router.delete("/api/user/settings/api-key/{provider}")
def delete_api_key(provider: str, username: str = Depends(current_user)):
    data = us.load(username)
    data.get("api_keys", {}).pop(provider, None)
    us.save(username, data)
    return {"ok": True}


# ── SFTP sources ──────────────────────────────────────────────────────────────

@router.get("/api/user/sftp-sources")
def list_sftp_sources(username: str = Depends(current_user)):
    sources = us.get_sftp_sources(username)
    return {"sources": [us.safe_settings({"sftp_sources": [s]})["sftp_sources"][0]
                        for s in sources]}


class SFTPSourceBody(BaseModel):
    id: str
    label: str
    host: str
    port: int = 22
    username: str
    gateway_base: str
    filename_patterns: list[str] = ["*.pdf"]
    auth_method: str = "key"         # "key" | "password"
    private_key: str | None = None   # PEM content
    password: str | None = None
    passphrase: str | None = None
    enabled: bool = True


@router.post("/api/user/sftp-sources")
def upsert_sftp_source(body: SFTPSourceBody, username: str = Depends(current_user)):
    if not body.id or not body.id.replace("_", "").isalnum():
        raise HTTPException(400, "id must be alphanumeric (underscores allowed)")
    source = body.model_dump()
    source["type"] = "sftp"
    us.upsert_sftp_source(username, source)
    return {"ok": True, "id": body.id}


@router.delete("/api/user/sftp-sources/{source_id}")
def delete_sftp_source(source_id: str, username: str = Depends(current_user)):
    us.delete_sftp_source(username, source_id)
    return {"ok": True}
