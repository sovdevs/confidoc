"""SFTP Secure Gateway API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.services.source_config_loader import load_sources, safe_source

router = APIRouter()


def _sftp_sources() -> list[dict]:
    """Return all enabled SFTP sources that have a gateway_base configured."""
    return [
        s for s in load_sources()
        if s.get("type") == "sftp"
        and s.get("enabled", True)
        and s.get("gateway_base")
    ]


def _require_sftp_source(source_id: str) -> dict:
    for s in _sftp_sources():
        if s["id"] == source_id:
            return s
    raise HTTPException(404, f"SFTP gateway source '{source_id}' not found or not configured")


def _make_gw(source_id: str):
    from app.services.gateway_sftp import SFTPGateway
    return SFTPGateway(_require_sftp_source(source_id))


# ── Sources ───────────────────────────────────────────────────────────────────

@router.get("/api/gateway/sftp/sources")
def list_sftp_gateway_sources():
    """Return SFTP sources that have gateway_base configured (no credentials)."""
    return {"sources": [safe_source(s) for s in _sftp_sources()]}


# ── Per-source endpoints ──────────────────────────────────────────────────────

@router.post("/api/gateway/sftp/{source_id}/test")
def sftp_test(source_id: str):
    gw = _make_gw(source_id)
    return gw.test_connection()


@router.post("/api/gateway/sftp/{source_id}/ensure-dirs")
def sftp_ensure_dirs(source_id: str):
    """Create the remote gateway folder structure if missing."""
    gw = _make_gw(source_id)
    return gw.ensure_remote_dirs()


@router.get("/api/gateway/sftp/{source_id}/status")
def sftp_status(source_id: str):
    gw = _make_gw(source_id)
    from app.config import settings
    try:
        counts = gw.remote_counts()
    except Exception as exc:
        counts = {"error": str(exc)[:200]}
    return {
        "ok":            True,
        "source_id":     source_id,
        "auto_approve":  settings.auto_approve_gateway_jobs,
        "counts":        counts,
        "recent_events": gw.load_registry(limit=30),
        "remote_base":   gw.r_base,
    }


@router.post("/api/gateway/sftp/{source_id}/scan")
def sftp_scan(source_id: str):
    gw = _make_gw(source_id)
    try:
        files = gw.scan_incoming()
        return {
            "ok":        True,
            "files":     files,
            "count":     len(files),
            "actionable": sum(1 for f in files if f["supported"] and f["requires_ocr"]),
        }
    except Exception as exc:
        raise HTTPException(502, str(exc)[:300])


@router.post("/api/gateway/sftp/{source_id}/process-next")
async def sftp_process_next(source_id: str):
    """Foreground: pull and process the first incoming file."""
    gw = _make_gw(source_id)
    try:
        files = gw.scan_incoming()
    except Exception as exc:
        raise HTTPException(502, str(exc)[:300])
    ready = [f for f in files if f["supported"] and f["requires_ocr"]]
    if not ready:
        return {"ok": False, "message": "No supported files in remote incoming/"}
    return await gw.process_file(ready[0]["filename"])


@router.post("/api/gateway/sftp/{source_id}/process-all")
async def sftp_process_all(source_id: str, background_tasks: BackgroundTasks):
    """Background batch: always manual review mode, returns immediately."""
    gw = _make_gw(source_id)
    try:
        files = gw.scan_incoming()
    except Exception as exc:
        raise HTTPException(502, str(exc)[:300])
    ready = [f for f in files if f["supported"] and f["requires_ocr"]]
    if not ready:
        return {"ok": False, "message": "No supported files in remote incoming/"}

    batch_id = gw.batch_start([f["filename"] for f in ready])
    background_tasks.add_task(_run_sftp_batch, source_id, [f["filename"] for f in ready])
    return {"ok": True, "started": len(ready), "batch_id": batch_id}


async def _run_sftp_batch(source_id: str, filenames: list[str]) -> None:
    from app.services.gateway_sftp import SFTPGateway
    gw = SFTPGateway(_require_sftp_source(source_id))
    for filename in filenames:
        result = await gw.process_file(filename, force_manual=True)
        gw.batch_update(result)
    gw.batch_finish()


@router.get("/api/gateway/sftp/{source_id}/batch-status")
def sftp_batch_status(source_id: str):
    gw = _make_gw(source_id)
    return {"batch": gw.batch_status()}
