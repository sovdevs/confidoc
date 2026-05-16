"""Ingest stage: render PDF pages via PyMuPDF → BYOK vision LLM → extracted markdown.

Replaces the pdf_to_markdown.pipeline.run_batch() call with our own extraction
loop so that:
  - Scanned (image-only) PDFs are handled correctly via vision LLM.
  - The LLM provider is configurable via BYOK (openrouter / openai / localhost).
  - The extraction prompt is appropriate for medical documents, not hard-wired
    to Italian urban planning (the original pdf_to_markdown use case).

pdf_to_markdown is still used for export utilities (md_to_segments, write_tmx,
write_csv) — only its internal LLM pipeline is bypassed here.
"""

import asyncio
import re
from pathlib import Path

from app.config import settings
from app.services import llm_adapter
from app.storage import audit_log, jobs as job_store
from app.storage.jobs import Job, JobStatus

# ── Medical extraction prompt ─────────────────────────────────────────────────
# Operates on page images. Output language = source language (no translation).
# Anonymization is a separate downstream stage — do NOT redact here.

_SYSTEM_PROMPT = """\
You are a document extraction assistant specializing in medical and clinical documents.
Convert this scanned document page into clean, structured Markdown.

STRICT RULES:
1. Return ONLY valid Markdown — no explanations, no commentary outside the document text.
2. Preserve ALL text VERBATIM in its original language. Do NOT translate, summarise, or omit.
3. Do NOT anonymize, redact, or alter names, dates, addresses, or any identifiers.
   Anonymization is handled by a separate downstream stage.
4. Use ## for major section headings, ### for sub-sections.
5. Render tabular data as pipe tables (| col | col |) with a separator row after the header.
6. Use - for bullet lists; preserve original numbering for numbered lists.
7. Mark genuinely illegible text as exactly: [illegible]
8. Remove standalone page numbers (a lone digit or short number on its own line).
9. Do NOT invent, guess, or fill in missing content.
10. For the FIRST PAGE ONLY prepend YAML front matter between --- delimiters:
    document_type: (e.g. Arztbrief / Befundbericht / Entlassbrief / Laborbefund)
    date: DD.MM.YYYY if visible, else UNKNOWN
    institution: issuing institution if visible, else UNKNOWN
    language: primary language code (de / en / fr / it etc.)
"""


def _page_prompt(page_num: int, total: int) -> str:
    yaml_note = (
        "Include YAML front matter before any headings."
        if page_num == 1
        else "Do NOT include YAML front matter — first page only."
    )
    return (
        f"Page {page_num} of {total}. "
        f"Convert this medical document page to Markdown. {yaml_note}"
    )


def _strip_fences(text: str) -> str:
    """Remove code fences that some models wrap the response in."""
    text = text.strip()
    text = re.sub(r"^```(?:markdown|yaml)?\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    return text.strip()


async def _extract_pages(pdf_path: Path, job: Job) -> str:
    """Render each PDF page as PNG, send to vision LLM, return assembled markdown."""
    import fitz  # PyMuPDF

    with fitz.open(str(pdf_path)) as doc:
        total = len(doc)
        audit_log.log(job.id, "extraction_pages_detected", {"pages": total})

        semaphore = asyncio.Semaphore(settings.max_concurrent_pages)

        async def _one(page_num: int, fitz_page) -> str:
            # 144 DPI (2× scale) — good quality for medical text without huge payloads
            pix = fitz_page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
            image_bytes = pix.tobytes("png")
            async with semaphore:
                result = await llm_adapter.pdf_complete_vision(
                    images=[image_bytes],
                    text_prompt=_page_prompt(page_num, total),
                    system=_SYSTEM_PROMPT,
                )
            return _strip_fences(result)

        pages_md = await asyncio.gather(
            *[_one(i + 1, page) for i, page in enumerate(doc)]
        )

    return "\n\n".join(pages_md)


async def run(job: Job, pdf_bytes: bytes) -> Job:
    pdf_path = settings.input_dir / job.filename
    pdf_path.write_bytes(pdf_bytes)
    audit_log.log(job.id, "pdf_saved", {"path": str(pdf_path)})

    job_store.update_status(job.id, JobStatus.extracting)
    audit_log.log(job.id, "extraction_started", {
        "pdf_provider": settings.pdf_provider,
        "pdf_model": settings.pdf_model,
    })

    try:
        markdown = await _extract_pages(pdf_path, job)
    except Exception as e:
        job_store.update_status(job.id, JobStatus.failed, error=str(e))
        audit_log.log(job.id, "extraction_failed", {"error": str(e)})
        raise

    md_path = settings.extracted_dir / (Path(job.filename).stem + ".md")
    md_path.write_text(markdown, encoding="utf-8")
    rel = str(md_path.relative_to(settings.jobs_dir.parent))

    job_store.update_status(job.id, JobStatus.reviewing, extracted_md=rel)
    audit_log.log(job.id, "extraction_done", {"md": rel, "chars": len(markdown)})

    return job_store.load(job.id)
