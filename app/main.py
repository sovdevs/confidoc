"""Confidoc server entry point."""

import json
import logging
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.review_ui.routes import router

logger = logging.getLogger(__name__)


def _migrate_approved_terms() -> None:
    """Rename approved_terms.jsonl if it contains raw entity text (PHI leak).

    Detects the old format by checking for a 'text' field in the first entry.
    The unsafe file is renamed to approved_terms.unsafe.backup.jsonl and a
    fresh empty file is created. No data is read or processed further.
    """
    p = settings.approved_terms
    if not p.exists() or p.stat().st_size == 0:
        return
    try:
        first_line = p.open(encoding="utf-8").readline()
        entry = json.loads(first_line)
        if "text" not in entry:
            return  # already clean format
    except Exception:
        return  # can't parse — leave it alone

    backup = p.parent / "approved_terms.unsafe.backup.jsonl"
    p.rename(backup)
    p.touch()
    p.chmod(0o600)
    logger.warning(
        "approved_terms.jsonl contained raw entity text (PHI). "
        "Renamed to %s. A clean file has been created.", backup
    )


_migrate_approved_terms()


def _check_auth_config() -> None:
    """Warn loudly about missing users or demo credentials still in place."""
    import json as _json

    p = settings.users_file
    if not p.exists() or p.stat().st_size == 0:
        logger.warning(
            "NO USERS CONFIGURED — all protected endpoints will return 401. "
            "Create a user: uv run confidoc-auth create-user <username>"
        )
        return

    try:
        users = _json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return

    demo_usernames = {"admin", "test", "demo", "root"}
    flagged = [u["username"] for u in users if u.get("username") in demo_usernames]
    if flagged:
        logger.warning(
            "SECURITY WARNING: demo/default username(s) detected in users.json: %s. "
            "Delete and recreate with a proper username before deploying: "
            "uv run confidoc-auth delete-user <username> && "
            "uv run confidoc-auth create-user <username>",
            ", ".join(flagged),
        )


def _provision_demo_users() -> None:
    """Create users from CONFIDOC_DEMO_USERS=user1:pass1,user2:pass2 if not present.

    Existing users are never overwritten — safe to leave set permanently.
    Passwords are visible in the Render env-var dashboard; use for demo only.
    """
    import os as _os
    spec = _os.getenv("CONFIDOC_DEMO_USERS", "").strip()
    if not spec:
        logger.debug("CONFIDOC_DEMO_USERS not set — skipping user provisioning")
        return
    logger.info("CONFIDOC_DEMO_USERS found — provisioning %d user(s)", spec.count(",") + 1)
    from app.auth.users import create_user, get_user
    for pair in spec.split(","):
        pair = pair.strip()
        if ":" not in pair:
            continue
        username, _, password = pair.partition(":")
        username = username.strip()
        password = password.strip()
        if not username or not password:
            continue
        if get_user(username):
            logger.info("Demo user '%s' already exists — skipping", username)
            continue
        try:
            create_user(username, password)
            logger.info("Auto-provisioned demo user: %s", username)
        except Exception as exc:
            logger.warning("Could not provision demo user %s: %s", username, exc)


_provision_demo_users()
_check_auth_config()

app = FastAPI(title="Confidoc — Secure Document Pipeline")

from app.auth.middleware import AuthMiddleware
from app.auth.routes import router as auth_router
from app.api.user_settings import router as user_settings_router
app.add_middleware(AuthMiddleware)
app.include_router(auth_router)
app.include_router(user_settings_router)

app.include_router(router)

from app.api.server_sources import router as server_sources_router
app.include_router(server_sources_router)

from app.api.gateway import router as gateway_router
app.include_router(gateway_router)

from app.api.gateway_sftp import router as gateway_sftp_router
app.include_router(gateway_sftp_router)

_static = Path(__file__).parent / "review_ui" / "static"
if _static.exists():
    app.mount("/static", StaticFiles(directory=str(_static)), name="static")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/config")
def config():
    """Return non-sensitive runtime configuration for the UI."""
    return {
        "pdf_provider": settings.pdf_provider,
        "pdf_model":    settings.pdf_model,
        "anon_provider": settings.anon_provider,
        "anon_model":    settings.anon_model,
        "byok_only":          settings.byok_only,
        "demo_capture":       settings.demo_capture,
        "demo_key_available": bool(settings.demo_api_key),
    }


@app.get("/api/models")
async def list_models(
    provider: str = Query("openrouter"),
    api_key: str  = Query(""),
):
    """Proxy the provider's model list, filtered to vision-capable models."""
    # For Google direct, prefer the anon key (text tasks) over the pdf key
    if provider == "google":
        key = api_key or settings.anon_api_key or settings.pdf_api_key
    else:
        key = api_key or settings.pdf_api_key

    if provider == "openrouter":
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://openrouter.ai/api/v1/models",
                    headers={"Authorization": f"Bearer {key}"} if key else {},
                )
                r.raise_for_status()
                data = r.json().get("data", [])
        except Exception as e:
            return {"models": [], "error": str(e)}

        vision = [
            {"id": m["id"], "name": m.get("name", m["id"])}
            for m in data
            if "image" in m.get("architecture", {}).get("modality", "")
        ]
        vision.sort(key=lambda m: m["id"])
        return {"models": vision}

    if provider == "openai":
        # Return a curated static list — OpenAI's key won't be the configured key
        return {"models": [
            {"id": "gpt-4o",                   "name": "GPT-4o"},
            {"id": "gpt-4o-mini",              "name": "GPT-4o mini"},
            {"id": "gpt-4-turbo",              "name": "GPT-4 Turbo"},
            {"id": "gpt-4-vision-preview",     "name": "GPT-4 Vision (legacy)"},
        ]}

    if provider == "google":
        # Fetch live from Google's model list — filters to multimodal (vision) models
        if not key:
            return {"models": [], "error": "Enter your Google API key to load available models"}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"key": key},
                )
                r.raise_for_status()
                data = r.json().get("models", [])
        except httpx.HTTPStatusError as e:
            try:
                detail = e.response.json()
                msg = detail.get("error", {}).get("message") or str(e)
            except Exception:
                msg = str(e)
            if e.response.status_code in (400, 401, 403):
                msg += " — Google needs an AIza… key from aistudio.google.com/app/apikey"
            return {"models": [], "error": msg}
        except Exception as e:
            return {"models": [], "error": str(e)}

        # Keep only generateContent-capable models with multimodal input
        vision = []
        for m in data:
            if "generateContent" not in m.get("supportedGenerationMethods", []):
                continue
            input_types = m.get("supportedActions", {})
            # Gemini models with vision support have "IMAGE" in inputTokenLimit description
            # Safer: include all generateContent models — the Flash/Pro family all support images
            name_full = m.get("name", "")           # "models/gemini-2.0-flash"
            model_id  = name_full.removeprefix("models/")
            display   = m.get("displayName", model_id)
            if model_id:
                vision.append({"id": model_id, "name": display})
        vision.sort(key=lambda m: m["id"])
        return {"models": vision}

    # localhost / unknown — no API to query
    return {"models": []}


def main() -> None:
    import os as _os
    from pathlib import Path as _Path
    app_dir = str(_Path(__file__).parent)
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
        reload_dirs=[app_dir],   # only watch app/ — not data/ which changes during processing
    )


if __name__ == "__main__":
    main()
