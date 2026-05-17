"""Server Source Ingest API — pull documents from remote sources into Zone 1."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.source_config_loader import get_source_by_id, load_sources, safe_source
from app.services.source_ingest_service import annotate_file_list, pull_files
from app.storage import audit_log

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _file_dict(f) -> dict:
    return {
        "remote_id":   f.remote_id,
        "filename":    f.filename,
        "remote_path": f.remote_path,
        "extension":   f.extension,
        "size_bytes":  f.size_bytes,
        "modified_at": f.modified_at,
        "status":      f.status,
        "supported":   f.supported,
        "requires_ocr": f.requires_ocr,
    }


def _require_source(source_id: str) -> dict:
    cfg = get_source_by_id(source_id)
    if not cfg:
        raise HTTPException(404, f"Source '{source_id}' not found")
    if not cfg.get("enabled", True):
        raise HTTPException(403, f"Source '{source_id}' is disabled")
    return cfg


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/api/server-sources")
def list_server_sources():
    """List configured sources — credentials stripped."""
    sources = [safe_source(s) for s in load_sources() if s.get("enabled", True)]
    return {"sources": sources}


@router.post("/api/server-sources/{source_id}/test")
def test_source(source_id: str):
    """Test connectivity for a source without exposing credentials."""
    cfg = _require_source(source_id)
    try:
        from app.connectors import get_connector
        result = get_connector(cfg).test()
        return {"ok": result.ok, "message": result.message}
    except Exception as exc:
        return {"ok": False, "message": str(exc)[:300]}


@router.get("/api/server-sources/{source_id}/files")
def list_source_files(
    source_id: str,
    include_seen: bool = Query(True),
    pattern: str = Query(None),
):
    """List remote files with new / seen / changed / unsupported status."""
    cfg = _require_source(source_id)
    try:
        from app.connectors import get_connector
        files = get_connector(cfg).list_files(pattern=pattern)
        files = annotate_file_list(source_id, files)
        if not include_seen:
            files = [f for f in files if f.status != "seen"]
        audit_log.log("server", "SOURCE_LIST_FILES", {
            "source_id": source_id,
            "count": len(files),
        })
        return {"source_id": source_id, "files": [_file_dict(f) for f in files]}
    except Exception as exc:
        raise HTTPException(502, f"Remote listing failed: {str(exc)[:300]}")


class PullUnseenBody(BaseModel):
    pattern: str | None = None
    limit: int = 100
    src_lang: str = "de-DE"
    tgt_lang: str = "en-GB"


@router.post("/api/server-sources/{source_id}/pull-unseen")
def pull_unseen(source_id: str, body: PullUnseenBody):
    """Pull all new/changed files from the source into Zone 1."""
    cfg = _require_source(source_id)
    try:
        from app.connectors import get_connector
        files = get_connector(cfg).list_files(pattern=body.pattern)
        files = annotate_file_list(source_id, files)
        to_pull = [f for f in files if f.status in ("new", "changed") and f.supported][: body.limit]
        return pull_files(cfg, to_pull, src_lang=body.src_lang, tgt_lang=body.tgt_lang)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Pull failed: {str(exc)[:300]}")


class PullSelectedBody(BaseModel):
    remote_paths: list[str]
    src_lang: str = "de-DE"
    tgt_lang: str = "en-GB"


@router.post("/api/server-sources/{source_id}/pull")
def pull_selected(source_id: str, body: PullSelectedBody):
    """Pull specific files by remote path."""
    cfg = _require_source(source_id)
    try:
        from app.connectors import get_connector
        all_files = get_connector(cfg).list_files()
        all_files = annotate_file_list(source_id, all_files)
        path_set  = set(body.remote_paths)
        to_pull   = [f for f in all_files if f.remote_path in path_set]
        if not to_pull:
            raise HTTPException(400, "None of the requested paths found on the remote source")
        return pull_files(cfg, to_pull, src_lang=body.src_lang, tgt_lang=body.tgt_lang)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Pull failed: {str(exc)[:300]}")
