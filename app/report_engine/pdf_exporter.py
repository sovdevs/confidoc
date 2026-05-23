"""Export HTML to PDF bytes via WeasyPrint."""
from __future__ import annotations


def render_pdf(html: str) -> bytes:
    try:
        from weasyprint import HTML
    except ImportError:
        raise RuntimeError(
            "weasyprint not installed. Add it to pyproject.toml and rebuild."
        )
    return HTML(string=html).write_pdf()
