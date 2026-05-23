"""Theme loading and CSS generation."""
from __future__ import annotations
import base64
import json
from pathlib import Path
from .models import Theme


def load(theme_path: Path) -> Theme:
    if theme_path.exists():
        try:
            return Theme.from_dict(json.loads(theme_path.read_text(encoding="utf-8")))
        except Exception:
            pass
    return Theme.default()


def logo_data_uri(logo: str, report_dir: Path) -> str:
    """Return a data URI if logo is a local file path; pass through if empty or URL."""
    if not logo:
        return ""
    p = report_dir / logo
    if p.exists():
        ext = p.suffix.lower().lstrip('.')
        mime = {'jpg': 'jpeg', 'jpeg': 'jpeg', 'png': 'png',
                'svg': 'svg+xml', 'gif': 'gif', 'webp': 'webp'}.get(ext, 'png')
        data = base64.b64encode(p.read_bytes()).decode()
        return f"data:image/{mime};base64,{data}"
    return logo  # already a URL


def build_css(theme: Theme) -> str:
    # Escape footer_text for use in CSS content property
    footer_escaped = theme.footer_text.replace("\\", "\\\\").replace('"', '\\"')
    return f"""
/* ── Report Engine Theme ─────────────────────────── */
:root {{
    --accent:  {theme.accent_color};
    --font:    {theme.font_family};
    --muted:   #666;
    --border:  #e2e8f0;
    --surface: #f8fafc;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}

body {{
    font-family: var(--font);
    font-size: 11pt;
    line-height: 1.65;
    color: #1a1a1a;
    background: #fff;
}}

.report-page {{
    max-width: 800px;
    margin: 0 auto;
    padding: 48px 56px;
}}

/* ── Header ──────────────────────────────────────── */
.report-header {{
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 24px;
    margin-bottom: 40px;
    padding-bottom: 20px;
    border-bottom: 3px solid var(--accent);
}}
.report-logo {{ max-height: 52px; max-width: 180px; object-fit: contain; }}
.report-title {{ font-size: 22pt; font-weight: 700; color: #111; line-height: 1.2; }}
.report-subtitle {{ font-size: 11pt; color: var(--muted); margin-top: 4px; }}

/* ── Typography ──────────────────────────────────── */
h1 {{ font-size: 16pt; color: #111; margin: 36px 0 10px;
      padding-bottom: 6px; border-bottom: 1px solid var(--border); }}
h2 {{ font-size: 13pt; color: #222; margin: 24px 0 8px; }}
h3 {{ font-size: 11pt; color: #333; margin: 16px 0 5px; font-weight: 700; }}
p  {{ margin-bottom: 12px; }}
a  {{ color: var(--accent); }}
ul, ol {{ margin: 0 0 12px 24px; }}
li {{ margin-bottom: 3px; }}
hr {{ border: none; border-top: 1px solid var(--border); margin: 28px 0; }}

blockquote {{
    margin: 16px 0;
    padding: 12px 18px;
    border-left: 4px solid var(--accent);
    background: var(--surface);
    color: #444;
    font-style: italic;
}}

code {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 3px;
    padding: 1px 5px;
    font-family: 'Courier New', monospace;
    font-size: 9.5pt;
}}

/* ── Tables ──────────────────────────────────────── */
table {{ width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 10pt; }}
th {{
    background: var(--accent);
    color: #fff;
    padding: 8px 12px;
    text-align: left;
    font-weight: 600;
    font-size: 9.5pt;
    text-transform: uppercase;
    letter-spacing: 0.3px;
}}
td {{ padding: 7px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }}
tr:last-child td {{ border-bottom: none; }}
tr:nth-child(even) td {{ background: #fafafa; }}

/* ── Directive components ────────────────────────── */
figure.report-asset {{
    margin: 24px 0;
    text-align: center;
    page-break-inside: avoid;
}}
figure.report-asset img {{
    max-width: 100%;
    height: auto;
    border-radius: 4px;
    border: 1px solid var(--border);
}}
figure.report-asset.width-half img {{ max-width: 55%; }}
figcaption {{
    font-size: 9.5pt;
    color: var(--muted);
    margin-top: 6px;
    font-style: italic;
}}

.kpi-grid {{
    display: flex;
    gap: 14px;
    margin: 24px 0;
    flex-wrap: wrap;
    page-break-inside: avoid;
}}
.kpi-card {{
    flex: 1;
    min-width: 110px;
    background: var(--surface);
    border-radius: 8px;
    padding: 16px 14px;
    text-align: center;
    border-top: 3px solid var(--accent);
}}
.kpi-value {{ font-size: 17pt; font-weight: 700; color: var(--accent); }}
.kpi-label {{
    font-size: 8.5pt;
    color: var(--muted);
    margin-top: 4px;
    text-transform: uppercase;
    letter-spacing: 0.4px;
}}

.callout {{
    margin: 18px 0;
    padding: 13px 17px;
    border-radius: 6px;
    border-left: 4px solid var(--accent);
    page-break-inside: avoid;
}}
.callout p {{ margin-bottom: 0; }}
.callout-note        {{ background: #eff6ff; border-color: var(--accent); }}
.callout-warning     {{ background: #fffbeb; border-color: #f59e0b; }}
.callout-info        {{ background: #f0fdf4; border-color: #22c55e; }}
.callout-owner_value {{ background: #fdf4ff; border-color: #a855f7; }}

.directive-error {{
    margin: 12px 0;
    padding: 10px 14px;
    background: #fff0f0;
    border: 1px solid #fca5a5;
    border-radius: 6px;
    color: #b91c1c;
    font-size: 9.5pt;
    font-family: monospace;
}}

.page-break {{ page-break-after: always; }}

/* ── Footer ──────────────────────────────────────── */
.report-footer {{
    margin-top: 48px;
    padding-top: 12px;
    border-top: 1px solid var(--border);
    font-size: 9pt;
    color: var(--muted);
    text-align: center;
}}

/* ── PDF / print ─────────────────────────────────── */
@media print {{
    .report-page {{ padding: 0; max-width: 100%; }}
}}
@page {{
    margin: 20mm 22mm 24mm;
    @bottom-center {{
        content: "{footer_escaped}";
        font-size: 8pt;
        color: #999;
        font-family: {theme.font_family};
    }}
}}
"""
