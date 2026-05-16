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

    # ── BYOK-only mode ────────────────────────────────────────────────────────
    # When True, requests without a user-supplied API key are rejected.
    # Set CONFIDOC_BYOK_ONLY=true on public/shared deployments so the server
    # key is never used — each user must bring their own.
    byok_only: bool = os.getenv("CONFIDOC_BYOK_ONLY", "false").lower() == "true"

    # ── Demo capture mode ─────────────────────────────────────────────────────
    # When True, pipeline artifacts are captured under data/demo_runs/ for
    # reproducible demo playback. Default off; never enable in production.
    demo_capture: bool = os.getenv("CONFIDOC_DEMO_CAPTURE", "false").lower() == "true"

    # Server-side key used exclusively for demo document processing.
    # Set this on Render so demo documents run without the viewer entering a key.
    # Regular BYOK uploads are unaffected — they still require the user's own key.
    demo_api_key: str = os.getenv("CONFIDOC_DEMO_API_KEY", "")

    # Demo input documents (synthetic only — no real PHI).
    # Always relative to the repo root, not DATA_DIR — these are static app
    # assets committed to git, not user-generated data on the mounted disk.
    demo_dir: Path = ROOT / "data" / "demo"
    # Demo run artifact storage (on the persistent disk alongside other runtime data)
    demo_runs_dir: Path = DATA / "demo_runs"

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
        dirs = [
            self.input_dir, self.extracted_dir, self.anonymized_dir,
            self.reviewed_dir, self.normalized_dir, self.exported_dir,
            self.final_dir, self.jobs_dir, self.mappings_dir,
            self.zone1_previews_dir,
        ]
        if self.demo_capture:
            dirs += [self.demo_runs_dir]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
