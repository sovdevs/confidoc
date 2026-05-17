"""Local folder gateway API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks

from app.config import settings
from app.services.gateway_local import LocalGateway

router = APIRouter()
_gw    = LocalGateway()


@router.get("/api/gateway/local/status")
def gateway_status():
    """Return counts and recent registry events."""
    return {
        "ok":          True,
        "auto_approve": settings.auto_approve_gateway_jobs,
        "counts":      _gw.counts(),
        "recent_events": _gw.load_registry(limit=30),
        "paths": {
            "incoming":   str(_gw.incoming),
            "processing": str(_gw.processing),
            "processed":  str(_gw.processed),
            "failed":     str(_gw.failed),
            "exports":    str(_gw.exports),
        },
    }


@router.post("/api/gateway/local/scan")
def gateway_scan():
    """Scan incoming/ and return list of files with status."""
    files = _gw.scan_incoming()
    return {
        "ok":    True,
        "files": files,
        "count": len(files),
        "actionable": sum(1 for f in files if f["supported"] and f["requires_ocr"]),
    }


@router.post("/api/gateway/local/process-next")
async def gateway_process_next():
    """Process the first supported file in incoming/ (foreground, respects auto-approve)."""
    files = _gw.scan_incoming()
    ready = [f for f in files if f["supported"] and f["requires_ocr"]]
    if not ready:
        return {"ok": False, "message": "No supported files in incoming/"}
    result = await _gw.process_file(ready[0]["filename"])
    return result


@router.post("/api/gateway/local/process-all")
async def gateway_process_all(background_tasks: BackgroundTasks):
    """Start background batch processing — always manual review mode, never auto-approves.

    Returns immediately. Poll /api/gateway/local/batch-status for progress.
    Jobs appear in the sidebar as each file completes.
    """
    files = _gw.scan_incoming()
    ready = [f for f in files if f["supported"] and f["requires_ocr"]]
    if not ready:
        return {"ok": False, "message": "No supported files in incoming/"}

    batch_id = _gw.batch_start([f["filename"] for f in ready])
    background_tasks.add_task(_run_batch, [f["filename"] for f in ready])
    return {"ok": True, "started": len(ready), "batch_id": batch_id}


async def _run_batch(filenames: list[str]) -> None:
    """Background task: process files sequentially, always in manual mode."""
    for filename in filenames:
        result = await _gw.process_file(filename, force_manual=True)
        _gw.batch_update(result)
    _gw.batch_finish()


@router.get("/api/gateway/local/batch-status")
def gateway_batch_status():
    """Return current batch progress (or None if no batch has run)."""
    status = _gw.batch_status()
    return {"batch": status}
