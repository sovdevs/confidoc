"""Local folder gateway API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

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
    """Process the first supported file in incoming/."""
    files = _gw.scan_incoming()
    ready = [f for f in files if f["supported"] and f["requires_ocr"]]
    if not ready:
        return {"ok": False, "message": "No supported files in incoming/"}
    result = await _gw.process_file(ready[0]["filename"])
    return result


@router.post("/api/gateway/local/process-all")
async def gateway_process_all():
    """Process all supported files in incoming/ sequentially."""
    files = _gw.scan_incoming()
    ready = [f for f in files if f["supported"] and f["requires_ocr"]]
    if not ready:
        return {"ok": False, "message": "No supported files in incoming/"}

    results: list[dict] = []
    for f in ready:
        result = await _gw.process_file(f["filename"])
        results.append(result)

    ok_count   = sum(1 for r in results if r.get("ok"))
    fail_count = len(results) - ok_count
    return {
        "ok":        True,
        "processed": len(results),
        "succeeded": ok_count,
        "failed":    fail_count,
        "results":   results,
    }
