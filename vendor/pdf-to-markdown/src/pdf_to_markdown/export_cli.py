"""CLI: convert .md files to TMX and/or CSV for translation workflows.

Usage:
    pdf2export --input output/ --format both --src-lang de-DE --tgt-lang en-GB
    pdf2export --input report.md --format tmx --src-lang de-DE --tgt-lang en-GB
"""

import argparse
import sys
from pathlib import Path

from pdf_to_markdown.exporter import md_to_segments, write_csv, write_tmx


def _collect_md_files(path: Path) -> list[Path]:
    if path.is_file() and path.suffix == '.md':
        return [path]
    if path.is_dir():
        return sorted(path.glob('*.md'))
    return []


def main() -> None:
    parser = argparse.ArgumentParser(description='Export Markdown to TMX/CSV for translation.')
    parser.add_argument('--input', type=Path, required=True,
                        help='A .md file or a folder containing .md files.')
    parser.add_argument('--output', type=Path, default=None,
                        help='Output folder (default: same location as input).')
    parser.add_argument('--format', choices=['tmx', 'csv', 'both'], default='both',
                        help='Output format (default: both).')
    parser.add_argument('--src-lang', default='de-DE',
                        help='Source language code, e.g. de-DE (default: de-DE).')
    parser.add_argument('--tgt-lang', default='en-GB',
                        help='Target language code, e.g. en-GB (default: en-GB).')
    args = parser.parse_args()

    md_files = _collect_md_files(args.input)
    if not md_files:
        print(f"No .md files found at '{args.input}'. Exiting.")
        sys.exit(1)

    out_dir = args.output or (args.input if args.input.is_dir() else args.input.parent)
    out_dir.mkdir(parents=True, exist_ok=True)

    ok, failed = 0, []
    for md_path in md_files:
        try:
            text = md_path.read_text(encoding='utf-8')
            segments = md_to_segments(text)
            if not segments:
                print(f"  SKIP {md_path.name} — no segments extracted")
                continue

            stem = md_path.stem
            if args.format in ('tmx', 'both'):
                write_tmx(segments, args.src_lang, args.tgt_lang, out_dir / f'{stem}.tmx')
            if args.format in ('csv', 'both'):
                write_csv(segments, args.src_lang, args.tgt_lang, out_dir / f'{stem}.csv')

            print(f"  OK  {md_path.name}  ({len(segments)} segments)")
            ok += 1
        except Exception as e:
            print(f"  ERR {md_path.name}: {e}")
            failed.append(md_path.name)

    print(f"\n{'-'*50}")
    print(f"Done. {ok} converted, {len(failed)} failed.")
    if failed:
        for f in failed:
            print(f"  {f}")
    sys.exit(0 if not failed else 1)


if __name__ == '__main__':
    main()
