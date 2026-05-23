"""Reports API — Confidoc wrapper around the reusable report_engine.

This file is allowed to import Confidoc internals. report_engine/ must not.
"""
from __future__ import annotations
import json
import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from app.config import settings
from app.report_engine import (
    ReportPackage, discover_assets, render_html, render_pdf, load_theme,
)

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _report_dir(job_id: str) -> Path:
    d = settings.reports_dir / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _exports_dir(job_id: str) -> Path:
    d = _report_dir(job_id) / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_package(job_id: str) -> ReportPackage:
    report_dir = _report_dir(job_id)
    md_path    = report_dir / "report.md"
    meta_path  = report_dir / "report.json"

    markdown = md_path.read_text(encoding="utf-8") if md_path.exists() else ""

    meta: dict = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    theme  = load_theme(report_dir / "theme.json")
    assets = discover_assets(report_dir)

    return ReportPackage(
        markdown=markdown,
        assets=assets,
        theme=theme,
        report_dir=report_dir,
        title=meta.get("title", "Report"),
        subtitle=meta.get("subtitle", ""),
    )


# ── Package CRUD ──────────────────────────────────────────────────────────────

@router.get("/api/jobs/{job_id}/report/package")
def get_package(job_id: str):
    report_dir = _report_dir(job_id)
    markdown   = (report_dir / "report.md").read_text(encoding="utf-8") \
                 if (report_dir / "report.md").exists() else ""
    meta: dict = {}
    meta_path  = report_dir / "report.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"markdown": markdown, "meta": meta}


class _SaveBody(BaseModel):
    markdown: str
    meta: dict = {}


@router.post("/api/jobs/{job_id}/report/package")
def save_package(job_id: str, body: _SaveBody):
    report_dir = _report_dir(job_id)
    md_path    = report_dir / "report.md"
    md_path.write_text(body.markdown, encoding="utf-8")
    md_path.chmod(0o600)
    if body.meta:
        mp = report_dir / "report.json"
        mp.write_text(json.dumps(body.meta, indent=2, ensure_ascii=False), encoding="utf-8")
        mp.chmod(0o600)
    return {"ok": True}


# ── Asset management ──────────────────────────────────────────────────────────

@router.get("/api/jobs/{job_id}/report/assets")
def list_assets(job_id: str):
    manifest = discover_assets(_report_dir(job_id))
    return {
        cat: [{"id": a.id, "path": a.path, "caption": a.caption} for a in items]
        for cat, items in [
            ("images",      manifest.images),
            ("charts",      manifest.charts),
            ("screenshots", manifest.screenshots),
            ("tables",      manifest.tables),
        ]
    }


@router.post("/api/jobs/{job_id}/report/assets/upload")
async def upload_asset(
    job_id: str,
    category: str = "images",
    file: UploadFile = File(...),
):
    if category not in ("images", "charts", "screenshots", "tables"):
        raise HTTPException(400, "category must be images | charts | screenshots | tables")
    dest_dir = _report_dir(job_id) / "assets" / category
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / file.filename
    dest.write_bytes(await file.read())
    dest.chmod(0o600)
    return {"ok": True, "path": f"assets/{category}/{file.filename}"}


@router.get("/api/jobs/{job_id}/report/assets/{path:path}")
def serve_asset(job_id: str, path: str):
    """Serve a specific asset file (for thumbnail previews in the UI)."""
    p = _report_dir(job_id) / path
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "Asset not found")
    mime, _ = mimetypes.guess_type(str(p))
    return Response(content=p.read_bytes(), media_type=mime or "application/octet-stream")


# ── Render / export ───────────────────────────────────────────────────────────

@router.post("/api/jobs/{job_id}/report/preview")
def preview(job_id: str):
    """Render the report to HTML for the in-browser iframe preview."""
    try:
        pkg  = _load_package(job_id)
        html = render_html(pkg)
    except Exception as e:
        raise HTTPException(400, f"Render error: {e}")
    return HTMLResponse(content=html)


@router.post("/api/jobs/{job_id}/report/export/pdf")
def export_pdf(job_id: str):
    try:
        pkg      = _load_package(job_id)
        html     = render_html(pkg)
        pdf_data = render_pdf(html)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(500, f"PDF export failed: {e}")

    out = _exports_dir(job_id) / "report.pdf"
    out.write_bytes(pdf_data)
    return Response(
        content=pdf_data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="report_{job_id[:8]}.pdf"'},
    )


@router.post("/api/jobs/{job_id}/report/export/html")
def export_html_file(job_id: str):
    try:
        pkg  = _load_package(job_id)
        html = render_html(pkg)
    except Exception as e:
        raise HTTPException(500, f"HTML export failed: {e}")

    out = _exports_dir(job_id) / "report.html"
    out.write_text(html, encoding="utf-8")
    return Response(
        content=html.encode("utf-8"),
        media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="report_{job_id[:8]}.html"'},
    )
