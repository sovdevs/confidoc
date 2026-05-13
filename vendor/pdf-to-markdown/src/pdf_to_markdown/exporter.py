"""Export parsed Markdown to TMX 1.4 or CSV for translation workflows."""

import csv
import io
import re
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape


# ── Markdown parsing ────────────────────────────────────────────────────────

_FRONT_MATTER_RE = re.compile(r'^\s*---\n.*?\n---\n', re.DOTALL)
_HTML_COMMENT_RE = re.compile(r'<!--.*?-->', re.DOTALL)
_HEADING_RE = re.compile(r'^#{1,6}\s+')
_TABLE_SEP_RE = re.compile(r'^\|[\s\-:|]+\|[\s\-:|]*$')
_INLINE_MD_RE = re.compile(
    r'\*\*(.+?)\*\*'       # **bold**
    r'|__(.+?)__'           # __bold__
    r'|\*(.+?)\*'           # *italic*
    r'|_(.+?)_'             # _italic_
    r'|`([^`]+)`'           # `code`
    r'|\[([^\]]+)\]\([^\)]+\)'  # [text](url)
)


def _strip_inline(text: str) -> str:
    def pick(m: re.Match) -> str:
        return next(g for g in m.groups() if g is not None)
    return _INLINE_MD_RE.sub(pick, text)


def _clean(text: str) -> str:
    return re.sub(r'\s+', ' ', _strip_inline(text)).strip()


def md_to_segments(text: str) -> list[str]:
    """Return a flat list of translation-ready segments from a Markdown string."""
    text = _FRONT_MATTER_RE.sub('', text)
    text = _HTML_COMMENT_RE.sub('', text)

    segments: list[str] = []
    para: list[str] = []

    def flush():
        if para:
            seg = _clean(' '.join(para))
            if seg:
                segments.append(seg)
            para.clear()

    for line in text.splitlines():
        stripped = line.strip()

        # Heading
        if _HEADING_RE.match(stripped):
            flush()
            seg = _clean(_HEADING_RE.sub('', stripped))
            if seg:
                segments.append(seg)
            continue

        # Table row
        if stripped.startswith('|'):
            flush()
            if _TABLE_SEP_RE.match(stripped):
                continue
            cells = [_clean(c) for c in stripped.split('|') if c.strip()]
            row = ' | '.join(c for c in cells if c)
            if row:
                segments.append(row)
            continue

        # Empty line → paragraph break
        if not stripped:
            flush()
            continue

        para.append(stripped)

    flush()
    return segments


# ── TMX writer ──────────────────────────────────────────────────────────────

_TMX_HEADER = """\
<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE tmx SYSTEM "tmx14.dtd">
<tmx version="1.4">
  <header creationtool="pdf-to-markdown" creationtoolversion="1.0"
          datatype="plaintext" segtype="paragraph"
          adminlang="en-US" srclang="{srclang}"
          creationdate="{date}"/>
  <body>
"""

_TMX_FOOTER = "  </body>\n</tmx>\n"


def _tmx_tu(tuid: int, seg: str, src_lang: str, tgt_lang: str) -> str:
    safe = escape(seg)
    return (
        f'    <tu tuid="{tuid}">\n'
        f'      <tuv xml:lang="{src_lang}"><seg>{safe}</seg></tuv>\n'
        f'      <tuv xml:lang="{tgt_lang}"><seg></seg></tuv>\n'
        f'    </tu>\n'
    )


def write_tmx(segments: list[str], src_lang: str, tgt_lang: str, out_path: Path) -> None:
    date = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    with out_path.open('w', encoding='utf-8') as f:
        f.write(_TMX_HEADER.format(srclang=src_lang, date=date))
        for i, seg in enumerate(segments, 1):
            f.write(_tmx_tu(i, seg, src_lang, tgt_lang))
        f.write(_TMX_FOOTER)


# ── CSV writer ───────────────────────────────────────────────────────────────

def write_csv(segments: list[str], src_lang: str, tgt_lang: str, out_path: Path) -> None:
    with out_path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerow(['id', src_lang, tgt_lang])
        for i, seg in enumerate(segments, 1):
            writer.writerow([i, seg, ''])
