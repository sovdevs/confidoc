from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
import os

load_dotenv()


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    gemini_model: str
    max_concurrent_pdfs: int
    max_concurrent_pages: int
    input_dir: Path
    output_dir: Path
    retry_dir: Path


def _require_api_key() -> str:
    # Accept either name; skip placeholder values from .env.example
    for key in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
        value = os.getenv(key, "").strip()
        if value and value != "your_key_here":
            return value
    raise ValueError("Set GOOGLE_API_KEY or GEMINI_API_KEY in your .env file.")


def load_settings(
    input_dir: Path | None = None,
    output_dir: Path | None = None,
    retry_dir: Path | None = None,
    max_concurrent_pdfs: int | None = None,
) -> Settings:
    return Settings(
        gemini_api_key=_require_api_key(),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
        max_concurrent_pdfs=max_concurrent_pdfs or int(os.getenv("MAX_CONCURRENT_PDFS", "5")),
        max_concurrent_pages=int(os.getenv("MAX_CONCURRENT_PAGES", "10")),
        input_dir=input_dir or Path(os.getenv("INPUT_DIR", "input")),
        output_dir=output_dir or Path(os.getenv("OUTPUT_DIR", "output")),
        retry_dir=retry_dir or Path(os.getenv("RETRY_DIR", "retry")),
    )
