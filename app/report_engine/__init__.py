"""Reusable Markdown-first report renderer.

No Confidoc-specific imports. The Confidoc API router wraps this module.
Usable standalone: pass a ReportPackage, get back HTML or PDF bytes.
"""
from .models import ReportPackage, AssetManifest, AssetItem, Theme
from .assets import discover as discover_assets
from .html_renderer import render_html
from .pdf_exporter import render_pdf
from .theme import load as load_theme

__all__ = [
    "ReportPackage", "AssetManifest", "AssetItem", "Theme",
    "discover_assets", "render_html", "render_pdf", "load_theme",
]
