"""Load settings from .env and expose typed config."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent.parent
# DATA_DIR env var lets cloud deployments point to a persistent mounted disk
DATA = Path(os.getenv("DATA_DIR", str(ROOT / "data")))


class Settings:
    # ── Legacy / fallback key ─────────────────────────────────────────────────
    # Used as the default API key when a profile-specific key is not set.
    google_api_key: str = os.getenv("GOOGLE_API_KEY", "")

    # ── PDF extraction profile ────────────────────────────────────────────────
    # Handles scanned page → Markdown via vision LLM.
    # Default: Gemini 2.0 Flash via OpenRouter.
    pdf_provider: str = os.getenv("CONFIDOC_PDF_PROVIDER", "openrouter")
    pdf_model: str    = os.getenv("CONFIDOC_PDF_MODEL",    "google/gemini-2.0-flash")
    pdf_api_key: str  = os.getenv("CONFIDOC_PDF_API_KEY",  os.getenv("OPENROUTER_API_KEY", os.getenv("GOOGLE_API_KEY", "")))
    pdf_base_url: str = os.getenv("CONFIDOC_PDF_BASE_URL", "")  # for localhost provider

    # ── Anonymization / PII detection profile ─────────────────────────────────
    # Handles the LLM PII detection pass (text-only, JSON output).
    anon_provider: str = os.getenv("CONFIDOC_ANON_PROVIDER", "openrouter")
    anon_model: str    = os.getenv("CONFIDOC_ANON_MODEL",    "google/gemini-2.0-flash")
    anon_api_key: str  = os.getenv("CONFIDOC_ANON_API_KEY",  os.getenv("OPENROUTER_API_KEY", os.getenv("GOOGLE_API_KEY", "")))
    anon_base_url: str = os.getenv("CONFIDOC_ANON_BASE_URL", "")

    # ── Concurrency ───────────────────────────────────────────────────────────
    max_concurrent_pdfs: int  = int(os.getenv("MAX_CONCURRENT_PDFS",  "3"))
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
    mappings_dir: Path = DATA / "mappings"       # encrypted per-job token maps
    zone1_previews_dir: Path = DATA / "zone1" / "previews"  # per-job page PNGs
    audit_log: Path = DATA / "audit.jsonl"
    approved_terms: Path = DATA / "approved_terms.jsonl"

    def ensure_dirs(self) -> None:
        for d in (
            self.input_dir, self.extracted_dir, self.anonymized_dir,
            self.reviewed_dir, self.normalized_dir, self.exported_dir,
            self.final_dir, self.jobs_dir, self.mappings_dir,
            self.zone1_previews_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
