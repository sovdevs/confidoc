import asyncio
import logging
import shutil
import traceback
from pathlib import Path

import yaml
from pdf_to_markdown.assembler import assemble, extract_front_matter
from pdf_to_markdown.config import Settings
from pdf_to_markdown.extractor import extract_pages
from pdf_to_markdown.llm import process_page
from pdf_to_markdown.validator import validate_document

logger = logging.getLogger(__name__)


async def process_pdf(pdf_path: Path, settings: Settings, page_semaphore: asyncio.Semaphore) -> Path | None:
    try:
        pages = extract_pages(pdf_path)
        total = len(pages)
        logger.info(f"[{pdf_path.name}] Extracted {total} page(s), sending to Gemini…")

        tasks = [process_page(p, total, settings, page_semaphore) for p in pages]
        pages_md = await asyncio.gather(*tasks)

        markdown = assemble(list(pages_md), pdf_path.name)
        metadata, body = extract_front_matter(markdown)
        fixed_body, warnings = validate_document(body, metadata)

        if warnings:
            for w in warnings:
                logger.warning(f"[{pdf_path.name}] {w}")

        # Re-assemble with fixed body
        yaml_block = yaml.dump(metadata, allow_unicode=True, default_flow_style=False).strip()
        final_markdown = f"---\n{yaml_block}\n---\n\n{fixed_body}\n"

        settings.output_dir.mkdir(parents=True, exist_ok=True)
        out_path = settings.output_dir / (pdf_path.stem + ".md")
        out_path.write_text(final_markdown, encoding="utf-8")

        print(f"[OK]   {pdf_path.name} → {out_path}")
        return out_path

    except Exception:
        tb = traceback.format_exc()
        logger.error(f"[FAIL] {pdf_path.name}\n{tb}")
        settings.retry_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf_path, settings.retry_dir / pdf_path.name)
        error_log = settings.retry_dir / (pdf_path.stem + "_error.log")
        error_log.write_text(tb, encoding="utf-8")
        print(f"[FAIL] {pdf_path.name} → {settings.retry_dir}  (see _error.log)")
        return None


async def run_batch(pdf_paths: list[Path], settings: Settings) -> dict:
    pdf_semaphore = asyncio.Semaphore(settings.max_concurrent_pdfs)
    page_semaphore = asyncio.Semaphore(settings.max_concurrent_pages)

    async def _guarded(path: Path) -> Path | None:
        async with pdf_semaphore:
            return await process_pdf(path, settings, page_semaphore)

    results = await asyncio.gather(*[_guarded(p) for p in pdf_paths])
    success = [r for r in results if r is not None]
    failed = [pdf_paths[i] for i, r in enumerate(results) if r is None]
    return {"success": success, "failed": failed}
