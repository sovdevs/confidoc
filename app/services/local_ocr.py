"""Local OCR engines — process page images on-machine, no cloud calls."""
from __future__ import annotations

import asyncio
import io
from concurrent.futures import ThreadPoolExecutor

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="localocr")

LOCAL_PROVIDERS = {"tesseract", "surya"}


# ── Tesseract ─────────────────────────────────────────────────────────────────

def _tesseract_sync(image_bytes: bytes, lang: str) -> str:
    try:
        import pytesseract
    except ImportError:
        raise RuntimeError(
            "pytesseract not installed. Run: pip install pytesseract pillow"
        )
    from PIL import Image
    img = Image.open(io.BytesIO(image_bytes))
    return pytesseract.image_to_string(img, lang=lang, config="--oem 3 --psm 3").strip()


async def tesseract_page(image_bytes: bytes, lang: str = "deu+eng") -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _tesseract_sync, image_bytes, lang)


# ── Surya OCR ─────────────────────────────────────────────────────────────────

_surya_models: dict | None = None


def _load_surya_models() -> dict:
    global _surya_models
    if _surya_models is not None:
        return _surya_models
    try:
        from surya.model.detection.model import load_model as det_m, load_processor as det_p
        from surya.model.recognition.model import load_model as rec_m
        from surya.model.recognition.processor import load_processor as rec_p
        _surya_models = {
            "det_model": det_m(), "det_proc": det_p(),
            "rec_model": rec_m(), "rec_proc": rec_p(),
        }
    except ImportError:
        raise RuntimeError(
            "surya-ocr not installed. Run: pip install surya-ocr"
        )
    return _surya_models


def _surya_sync(image_bytes: bytes) -> str:
    from surya.ocr import run_ocr
    from PIL import Image
    m = _load_surya_models()
    img = Image.open(io.BytesIO(image_bytes))
    results = run_ocr(
        [img], [None],
        m["det_model"], m["det_proc"],
        m["rec_model"], m["rec_proc"],
    )
    lines = [line.text for page in results for line in page.text_lines]
    return "\n".join(lines).strip()


async def surya_page(image_bytes: bytes) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _surya_sync, image_bytes)
