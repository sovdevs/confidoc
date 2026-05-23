"""Convert a ReportPackage into a styled HTML document.

Missing assets render as visible error blocks — never crash.
Unresolved/unknown directives render visibly in the preview.
"""
from __future__ import annotations
import base64
import csv
import io
import re
from html import escape as esc
from pathlib import Path

import markdown as _md

from .directives import DIRECTIVE_RE, parse
from .models import ReportPackage
from .theme import build_css, logo_data_uri


# ── Asset embedding ───────────────────────────────────────────────────────────

_MIME = {
    'jpg': 'jpeg', 'jpeg': 'jpeg', 'png': 'png',
    'gif': 'gif', 'webp': 'webp', 'svg': 'svg+xml',
}


def _img_uri(p: Path) -> str:
    ext = p.suffix.lower().lstrip('.')
    mime = _MIME.get(ext, 'png')
    data = base64.b64encode(p.read_bytes()).decode()
    return f"data:image/{mime};base64,{data}"


def _error(msg: str) -> str:
    return f'<div class="directive-error">⚠ {esc(msg)}</div>'


# ── Per-directive renderers ───────────────────────────────────────────────────

def _render_image(d: dict, report_dir: Path) -> str:
    path_str = (d['path'] or '').strip()
    caption   = d['kwargs'].get('caption', d['text'] or '')
    half      = d['kwargs'].get('width', '').lower() == 'half'
    cls       = 'width-half' if half else ''

    p = report_dir / path_str
    if not p.exists():
        return _error(f"Asset not found: {path_str}")
    try:
        src = _img_uri(p)
    except Exception as e:
        return _error(f"Could not read asset {path_str}: {e}")

    cap = f'<figcaption>{esc(caption)}</figcaption>' if caption else ''
    return f'<figure class="report-asset {cls}"><img src="{src}" alt="{esc(caption)}"/>{cap}</figure>'


def _render_table(d: dict, report_dir: Path) -> str:
    path_str = (d['path'] or '').strip()
    title     = d['kwargs'].get('title', '')

    p = report_dir / path_str
    if not p.exists():
        return _error(f"Table not found: {path_str}")
    try:
        rows = list(csv.reader(io.StringIO(p.read_text(encoding='utf-8'))))
    except Exception as e:
        return _error(f"Cannot parse table {path_str}: {e}")
    if not rows:
        return _error(f"Empty table: {path_str}")

    title_h = f'<p><strong>{esc(title)}</strong></p>' if title else ''
    head = '<tr>' + ''.join(f'<th>{esc(c)}</th>' for c in rows[0]) + '</tr>'
    body = ''.join(
        '<tr>' + ''.join(f'<td>{esc(c)}</td>' for c in row) + '</tr>'
        for row in rows[1:]
    )
    return f'{title_h}<table><thead>{head}</thead><tbody>{body}</tbody></table>'


def _render_kpi_grid(d: dict) -> str:
    if not d['kwargs']:
        return _error("kpi-grid has no values — add | Key=\"Value\" pairs")
    cards = ''.join(
        f'<div class="kpi-card">'
        f'<div class="kpi-value">{esc(v)}</div>'
        f'<div class="kpi-label">{esc(k)}</div>'
        f'</div>'
        for k, v in d['kwargs'].items()
    )
    return f'<div class="kpi-grid">{cards}</div>'


def _render_callout(d: dict) -> str:
    ctype = esc(d['kwargs'].get('type', 'note'))
    # Text comes from the positional part or a text= kwarg
    body  = d['text'] or d['kwargs'].get('text', '')
    if not body:
        return _error("callout has no text — add | your message after the type")
    return f'<div class="callout callout-{ctype}"><p>{esc(body)}</p></div>'


def _render_directive(d: dict, report_dir: Path) -> str:
    t = d['type']
    if t in ('image', 'chart', 'screenshot'):
        return _render_image(d, report_dir)
    if t == 'table':
        return _render_table(d, report_dir)
    if t == 'kpi-grid':
        return _render_kpi_grid(d)
    if t == 'callout':
        return _render_callout(d)
    if t == 'pagebreak':
        return '<div class="page-break"></div>'
    return _error(f"Unknown directive: {t}")


# ── Main renderer ─────────────────────────────────────────────────────────────

def _process_directives(text: str, report_dir: Path) -> str:
    def sub(m: re.Match) -> str:
        try:
            d = parse(m.group(1))
            return _render_directive(d, report_dir)
        except Exception as e:
            return _error(f"Directive parse error: {e}")
    return DIRECTIVE_RE.sub(sub, text)


def render_html(pkg: ReportPackage) -> str:
    """Return a complete HTML document string for the report package."""
    processed = _process_directives(pkg.markdown, pkg.report_dir)

    body_html = _md.markdown(
        processed,
        extensions=['tables', 'fenced_code'],
    )

    css       = build_css(pkg.theme)
    logo_uri  = logo_data_uri(pkg.theme.logo, pkg.report_dir)
    logo_html = (
        f'<img class="report-logo" src="{logo_uri}" alt="Logo"/>'
        if logo_uri else ''
    )
    subtitle_html = (
        f'<div class="report-subtitle">{esc(pkg.subtitle)}</div>'
        if pkg.subtitle else ''
    )
    footer_html = (
        f'<div class="report-footer">{esc(pkg.theme.footer_text)}</div>'
        if pkg.theme.footer_text else ''
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{esc(pkg.title)}</title>
<style>{css}</style>
</head>
<body>
<div class="report-page">
  <header class="report-header">
    {logo_html}
    <div>
      <div class="report-title">{esc(pkg.title)}</div>
      {subtitle_html}
    </div>
  </header>
  <main class="report-body">
    {body_html}
  </main>
  {footer_html}
</div>
</body>
</html>"""
