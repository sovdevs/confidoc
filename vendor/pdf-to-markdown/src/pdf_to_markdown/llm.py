import asyncio
import logging
import re

from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential

from pdf_to_markdown.config import Settings
from pdf_to_markdown.extractor import PageText

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a document structuring assistant. Your task is to convert raw text extracted from an \
Italian urban planning PDF into clean, structured Markdown.

STRICT RULES:
1. Return ONLY valid Markdown — no explanations, no prose outside the document text.
2. Preserve the Italian legal text VERBATIM. Never paraphrase, summarise, or omit content.
3. Map document structure to heading levels:
   - # for the document title (once, on page 1 only)
   - ## for TITOLO (Title) sections
   - ### for ART. / CAPO (Article / Chapter) headings
   - #### for numbered sub-articles (e.g. 2.1, 2.2) or paragraph headings
4. Do NOT skip heading levels (e.g. never go from # directly to ###).
5. Remove ALL of the following from the output:
   - Standalone page numbers (a lone digit or number on its own line, e.g. "2", "27", "134")
   - Repeated page headers/footers (document title, municipality name repeated on every page)
   - Copyright or authorship notices (lines containing "copyright", "vietato", "autori", "estensori")
6. Fix OCR artefacts: restore spaces between glued words (e.g. "superficieterritoriale" → "superficie territoriale").
7. Use pipe tables (| col | col |) for any tabular data. Add a separator row after the header.
8. For bullet lists use "- "; for numbered items keep original numbering (1. 2. or a) b)).
9. If a page is unreadable or nearly empty, output exactly: <!-- UNREADABLE: page {page_number} -->
10. TABLE OF CONTENTS pages: if the page is an index / table of contents (entries with dotted \
leaders and page numbers like "ART. 1 ......... 2"), format each entry as a plain list item \
"- entry text" and OMIT the page numbers and dot leaders entirely. Do NOT use heading markers \
(#, ##, ###) for TOC entries — plain "- " list items only.
11. YAML front matter: output it ONLY on page 1, between --- delimiters, with these fields:
    - titolo: the official document title
    - comune: the municipality name (e.g. "MARIANO COMENSE (CO)")
    - tipo: document type (e.g. NTA, Piano delle Regole, Regolamento Edilizio)
    - anno: the document approval or publication year as a 4-digit number (e.g. 2023).
      Look for a year in ranges like "(2022-2023)", "anno 2023", "approvato nel 2023", or \
      a standalone 4-digit number that is NOT part of a law citation (law citations look like \
      "L.R. 12/2005" or "D.Lgs. n.42/2004" — ignore years inside those).
      Use the most recent standalone year found. If none, write UNKNOWN.
"""

PAGE_PROMPT_TEMPLATE = """\
Page {page_number} of {total_pages}. \
Convert the raw extracted text below into Markdown following the system rules. \
{"Include the YAML front matter block (titolo, comune, tipo, anno) before the # title heading." if page_number == 1 else "Do NOT include YAML front matter — this is not page 1."}

--- RAW TEXT START ---
{raw_text}
--- RAW TEXT END ---
"""


def _make_page_prompt(page: PageText, total_pages: int) -> str:
    include_yaml = "Include the YAML front matter block (titolo, comune, tipo, anno) before the # title heading." \
        if page.page_number == 1 \
        else "Do NOT include YAML front matter — this is not page 1."
    return (
        f"Page {page.page_number} of {total_pages}. "
        f"Convert the raw extracted text below into Markdown following the system rules. "
        f"{include_yaml}\n\n"
        f"--- RAW TEXT START ---\n{page.text}\n--- RAW TEXT END ---"
    )


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    # Remove outer ```markdown ... ``` or ```yaml ... ``` wrapper
    text = re.sub(r"^```(?:markdown)?\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    # Convert ```yaml\n...\n``` front matter block to proper --- delimiters
    text = re.sub(r"^```yaml\s*\n(.*?)\n```", r"---\n\1\n---", text, flags=re.DOTALL)
    return text.strip()


def _is_retryable(exc: BaseException) -> bool:
    # Retry on server errors and transient failures; do NOT retry quota errors (429)
    msg = str(exc)
    if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
        return False
    return True


_retry = retry(
    retry=lambda rs: _is_retryable(rs.outcome.exception()) if rs.outcome.failed else False,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)


async def process_page(
    page: PageText,
    total_pages: int,
    settings: Settings,
    semaphore: asyncio.Semaphore,
) -> str:
    client = genai.Client(api_key=settings.gemini_api_key)
    prompt = _make_page_prompt(page, total_pages)

    @_retry
    async def _call() -> str:
        response = await client.aio.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.0,
                max_output_tokens=8192,
            ),
        )
        return _strip_code_fences(response.text)

    async with semaphore:
        logger.info(f"  Sending page {page.page_number}/{total_pages} to Gemini ({page.extraction_method})")
        result = await _call()
        logger.info(f"  Page {page.page_number}/{total_pages} done ({len(result)} chars)")
        return result
