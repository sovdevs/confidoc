"""Load settings from .env and expose typed config."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent.parent
# DATA_DIR env var lets cloud deployments point to a persistent mounted disk
DATA = Path(os.getenv("DATA_DIR", str(ROOT / "data")))


class Settings:
    google_api_key: str = os.getenv("GOOGLE_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    max_concurrent_pdfs: int = int(os.getenv("MAX_CONCURRENT_PDFS", "3"))
    max_concurrent_pages: int = int(os.getenv("MAX_CONCURRENT_PAGES", "5"))

    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = int(os.getenv("PORT", "8100"))

    # Data directories
    input_dir: Path = DATA / "input"
    extracted_dir: Path = DATA / "extracted"
    anonymized_dir: Path = DATA / "anonymized"
    reviewed_dir: Path = DATA / "reviewed"
    normalized_dir: Path = DATA / "normalized"
    exported_dir: Path = DATA / "exported"
    final_dir: Path = DATA / "final"
    jobs_dir: Path = DATA / "jobs"
    mappings_dir: Path = DATA / "mappings"   # encrypted per-job token maps
    audit_log: Path = DATA / "audit.jsonl"
    approved_terms: Path = DATA / "approved_terms.jsonl"

    def ensure_dirs(self) -> None:
        for d in (
            self.input_dir, self.extracted_dir, self.anonymized_dir,
            self.reviewed_dir, self.normalized_dir, self.exported_dir,
            self.final_dir, self.jobs_dir, self.mappings_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
