"""Parse {{ directive }} blocks from Markdown.

Directive syntax:
    {{ type[:path] [| key="value"]* [| plain text] }}

Examples:
    {{ image:assets/images/front.jpg | caption="Front view" | width="half" }}
    {{ chart:assets/charts/revenue.png | caption="Q1 Revenue" }}
    {{ table:assets/tables/summary.csv | title="Summary" }}
    {{ kpi-grid | Revenue="$48,200" | Occupancy="76%" }}
    {{ callout | type="note" | Text goes here }}
    {{ pagebreak }}
"""
from __future__ import annotations
import re

DIRECTIVE_RE = re.compile(r'\{\{(.+?)\}\}', re.DOTALL)


def parse(raw: str) -> dict:
    """Parse the content inside {{ … }} into a structured dict."""
    parts = [p.strip() for p in raw.strip().split('|')]

    first = parts[0]
    if ':' in first:
        dtype, _, path = first.partition(':')
        path = path.strip()
    else:
        dtype = first.strip()
        path = None

    kwargs: dict[str, str] = {}
    text_parts: list[str] = []
    for part in parts[1:]:
        m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_-]*)="(.*)"$', part, re.DOTALL)
        if m:
            kwargs[m.group(1)] = m.group(2)
        else:
            text_parts.append(part)

    return {
        'type': dtype.strip().lower(),
        'path': path,
        'kwargs': kwargs,
        'text': ' '.join(text_parts) if text_parts else None,
    }
