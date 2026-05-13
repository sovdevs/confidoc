import argparse
import asyncio
import logging
import sys
from pathlib import Path

from pdf_to_markdown.config import load_settings
from pdf_to_markdown.pipeline import run_batch

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert a folder of PDFs to Markdown using Gemini.")
    parser.add_argument("--input", type=Path, default=None, help="Folder containing PDF files (default: input/)")
    parser.add_argument("--output", type=Path, default=None, help="Destination folder for .md files (default: output/)")
    parser.add_argument("--retry", type=Path, default=None, help="Folder for failed PDFs (default: retry/)")
    parser.add_argument("--concurrency", type=int, default=None, help="Max parallel PDFs (default: from .env)")
    args = parser.parse_args()

    settings = load_settings(
        input_dir=args.input,
        output_dir=args.output,
        retry_dir=args.retry,
        max_concurrent_pdfs=args.concurrency,
    )

    pdf_paths = sorted(settings.input_dir.glob("*.pdf"))
    if not pdf_paths:
        print(f"No PDF files found in '{settings.input_dir}'. Exiting.")
        sys.exit(1)

    print(f"Found {len(pdf_paths)} PDF(s) in '{settings.input_dir}'. Processing…\n")
    summary = asyncio.run(run_batch(pdf_paths, settings))

    print(f"\n{'─'*50}")
    print(f"Done. {len(summary['success'])} succeeded, {len(summary['failed'])} failed.")
    if summary["failed"]:
        print("Failed files (see retry/ for error logs):")
        for f in summary["failed"]:
            print(f"  {f.name}")
    sys.exit(0 if not summary["failed"] else 1)


if __name__ == "__main__":
    main()
