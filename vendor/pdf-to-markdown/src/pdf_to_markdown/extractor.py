from dataclasses import dataclass
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class ExtractionError(Exception):
    pass


@dataclass
class PageText:
    page_number: int  # 1-based
    text: str
    extraction_method: str  # "docling" or "pymupdf"


def extract_with_docling(pdf_path: Path) -> list[PageText]:
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))
    doc = result.document

    pages: list[PageText] = []
    for page_no, page in doc.pages.items():
        # Export just this page's content as markdown text
        # We collect text elements belonging to this page
        texts = []
        for item, _ in doc.iterate_items():
            prov = getattr(item, "prov", None)
            if prov and any(p.page_no == page_no for p in prov):
                raw = getattr(item, "text", None)
                if raw:
                    texts.append(raw)

        page_text = "\n\n".join(texts)
        if page_text.strip():
            pages.append(PageText(
                page_number=page_no,
                text=page_text,
                extraction_method="docling",
            ))

    if not pages:
        raise ExtractionError(f"Docling returned no text for {pdf_path}")
    return pages


def _table_to_markdown(table) -> str:
    """Convert a PyMuPDF TableFinder result to pipe-table markdown."""
    rows = table.extract()
    if not rows:
        return ""
    lines = []
    for i, row in enumerate(rows):
        cells = [str(c).strip() if c is not None else "" for c in row]
        lines.append("| " + " | ".join(cells) + " |")
        if i == 0:
            lines.append("| " + " | ".join("---" for _ in cells) + " |")
    return "\n".join(lines)


def extract_with_pymupdf(pdf_path: Path) -> list[PageText]:
    import fitz  # PyMuPDF

    pages: list[PageText] = []
    with fitz.open(str(pdf_path)) as doc:
        for i, page in enumerate(doc, start=1):
            parts: list[str] = []

            # Detect and extract tables first using coordinate-aware finder
            try:
                tabs = page.find_tables()
                table_rects = []
                for tab in tabs.tables:
                    md = _table_to_markdown(tab)
                    if md:
                        parts.append(md)
                        table_rects.append(tab.bbox)
            except Exception:
                table_rects = []

            # Extract remaining text, clipping out table bounding boxes
            if table_rects:
                import fitz as _fitz
                clip = page.rect
                for bbox in table_rects:
                    # Mask table areas by extracting blocks outside them
                    pass
                # Use blocks to skip text inside table areas
                blocks = page.get_text("blocks", sort=True)
                for b in blocks:
                    bx0, by0, bx1, by1 = b[0], b[1], b[2], b[3]
                    in_table = any(
                        bx0 >= tb[0] - 2 and by0 >= tb[1] - 2
                        and bx1 <= tb[2] + 2 and by1 <= tb[3] + 2
                        for tb in table_rects
                    )
                    if not in_table:
                        txt = b[4].strip()
                        if txt:
                            parts.append(txt)
            else:
                txt = page.get_text("text")
                if txt.strip():
                    parts.append(txt)

            combined = "\n\n".join(p for p in parts if p.strip())
            if combined.strip():
                pages.append(PageText(
                    page_number=i,
                    text=combined,
                    extraction_method="pymupdf",
                ))

    if not pages:
        raise ExtractionError(f"PyMuPDF returned no text for {pdf_path}")
    return pages


def _is_sparse(pages: list[PageText], pdf_path: Path) -> bool:
    """Return True if Docling output looks suspiciously thin relative to file size."""
    total_chars = sum(len(p.text) for p in pages)
    file_kb = pdf_path.stat().st_size / 1024
    # Heuristic: expect at least ~50 chars per KB for a text-heavy PDF
    return total_chars < max(200, file_kb * 50)


def extract_pages(pdf_path: Path) -> list[PageText]:
    docling_err: Exception | None = None
    try:
        pages = extract_with_docling(pdf_path)
        if _is_sparse(pages, pdf_path):
            logger.warning(
                f"Docling output suspiciously sparse for {pdf_path.name} "
                f"({sum(len(p.text) for p in pages)} chars) — falling back to PyMuPDF"
            )
            raise ValueError("sparse output")
        logger.info(f"Docling extracted {len(pages)} page(s) from {pdf_path.name}")
        return pages
    except Exception as exc:
        docling_err = exc
        logger.warning(f"Docling failed for {pdf_path.name}: {exc} — falling back to PyMuPDF")

    try:
        pages = extract_with_pymupdf(pdf_path)
        logger.info(f"PyMuPDF extracted {len(pages)} page(s) from {pdf_path.name}")
        return pages
    except Exception as pymupdf_err:
        raise ExtractionError(
            f"Both extractors failed for {pdf_path.name}. "
            f"Docling: {docling_err}. PyMuPDF: {pymupdf_err}"
        ) from pymupdf_err
