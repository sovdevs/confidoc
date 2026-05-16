"""XLIFF / SDL XLIFF export for Confidoc translation packages.

Generates blank (source-only) XLIFF files from a list of plain-text segments.
All content is already anonymized (Zone 2 safe) before reaching these functions.

Two formats:
  xliff_12()      — standard XLIFF 1.2, compatible with most CAT tools
  sdlxliff_12()   — SDL XLIFF 1.2 (Trados Studio compatible), with mrk/seg-defs
"""

from __future__ import annotations


def _esc(text: str) -> str:
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def xliff_12(
    segments: list[str],
    src_lang: str,
    tgt_lang: str,
    original: str = "confidoc-export.md",
    package_id: str = "",
) -> bytes:
    """Standard XLIFF 1.2 — blank targets, state=new."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
    ]
    if package_id:
        lines.append(f'<!-- confidoc-package:{package_id} -->')
    lines += [
        '<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">',
        f'  <file source-language="{_esc(src_lang)}"'
        f' target-language="{_esc(tgt_lang)}"'
        f' original="{_esc(original)}"'
        f' datatype="plaintext">',
        "    <body>",
    ]
    for i, seg in enumerate(segments, 1):
        src = _esc(seg.strip())
        lines += [
            f'      <trans-unit id="{i}">',
            f'        <source>{src}</source>',
            f'        <target state="new"/>',
            f'      </trans-unit>',
        ]
    lines += ["    </body>", "  </file>", "</xliff>", ""]
    return "\n".join(lines).encode("utf-8")


def sdlxliff_12(
    segments: list[str],
    src_lang: str,
    tgt_lang: str,
    original: str = "confidoc-export.md",
    package_id: str = "",
) -> bytes:
    """SDL XLIFF 1.2 — blank targets with Trados seg-defs metadata.

    Produces a file importable by Trados Studio / Trados GroupShare as an
    untranslated document ready for translation assignment.
    """
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
    ]
    if package_id:
        lines.append(f'<!-- confidoc-package:{package_id} -->')
    lines += [
        '<xliff version="1.2"',
        '       xmlns="urn:oasis:names:tc:xliff:document:1.2"',
        '       xmlns:sdl="http://sdl.com/FileTypes/SdlXliff/1.0">',
        f'  <file original="{_esc(original)}"',
        f'        source-language="{_esc(src_lang)}"',
        f'        target-language="{_esc(tgt_lang)}"',
        f'        datatype="plaintext">',
        "    <body>",
    ]
    for i, seg in enumerate(segments, 1):
        src = _esc(seg.strip())
        lines += [
            f'      <trans-unit id="{i}" sdl:segment-count="1">',
            f'        <source xml:lang="{_esc(src_lang)}">{src}</source>',
            f'        <seg-source>',
            f'          <mrk mtype="seg" mid="{i}">{src}</mrk>',
            f'        </seg-source>',
            f'        <target xml:lang="{_esc(tgt_lang)}">',
            f'          <mrk mtype="seg" mid="{i}"/>',
            f'        </target>',
            f'        <sdl:seg-defs>',
            f'          <sdl:seg id="{i}" percent="0" origin="not-translated"',
            f'                   struct-match="false" text-match="false"/>',
            f'        </sdl:seg-defs>',
            f'      </trans-unit>',
        ]
    lines += ["    </body>", "  </file>", "</xliff>", ""]
    return "\n".join(lines).encode("utf-8")
