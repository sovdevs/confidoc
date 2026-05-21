"""Load settings from .env and expose typed config."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Try explicit paths first (Render Docker mounts secret files at the path
# you configure, relative to the container root — not the WORKDIR).
# Fall back to find_dotenv() for local dev.
def _load_env() -> None:
    candidates = [
        Path("/etc/secrets/.env"),              # Render Secret Files
        Path("/app/.env"),                      # Docker WORKDIR
        Path(__file__).parent.parent / ".env",  # local dev
    ]
    for p in candidates:
        if p.exists():
            try:
                load_dotenv(p, override=False)
            except Exception:
                # dotenv parse warning — load individual lines as fallback
                import re as _re
                for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    m = _re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$', line)
                    if m and m.group(1) not in os.environ:
                        os.environ[m.group(1)] = m.group(2).strip('"').strip("'")
            break

_load_env()

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

    # ── Demo mode ─────────────────────────────────────────────────────────────
    # demo_capture=true  → shows the Demo tab (playback) and enables capture endpoints
    # demo_capture_panel=true → also shows the sidebar "DEMO CAPTURE" controls
    #   (developer-only; leave false on deployed instances)
    demo_capture: bool = os.getenv("CONFIDOC_DEMO_CAPTURE", "false").lower() == "true"
    demo_capture_panel: bool = os.getenv("CONFIDOC_DEMO_CAPTURE_PANEL", "false").lower() == "true"

    # Server-side key used exclusively for demo document processing.
    # Set this on Render so demo documents run without the viewer entering a key.
    # Regular BYOK uploads are unaffected — they still require the user's own key.
    demo_api_key: str = os.getenv("CONFIDOC_DEMO_API_KEY", "")

    # Demo input documents (synthetic only — no real PHI).
    # Always relative to the repo root, not DATA_DIR — these are static app
    # assets committed to git, not user-generated data on the mounted disk.
    demo_dir: Path = ROOT / "data" / "demo"
    # Pre-captured demo run artifacts (committed to git — used for playback).
    # Always relative to the repo root so they're available inside the Docker image.
    demo_runs_dir: Path = ROOT / "data" / "demo_runs"

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
    prepared_packages_dir: Path = DATA / "prepared_packages"  # Zone 2 export packages
    llm_runs_dir: Path = DATA / "llm_runs"                   # LLM export run artifacts
    source_configs_dir: Path = DATA / "source_configs"       # sources.json (operator-managed)
    ingest_registry_path: Path = DATA / "zone1" / "ingest_registry.jsonl"
    gateway_local_dir: Path = DATA / "gateway" / "local"
    gateway_sftp_dir:  Path = DATA / "gateway" / "sftp"

    # ── Auth ──────────────────────────────────────────────────────────────────
    auth_enabled: bool = os.getenv("CONFIDOC_AUTH_ENABLED", "true").lower() == "true"
    strict_auth_mode: bool = os.getenv("STRICT_AUTH_MODE", "false").lower() == "true"
    session_ttl_hours: int = int(os.getenv("SESSION_TTL_HOURS", "72"))

    auth_dir: Path = DATA / "auth"
    users_file: Path = DATA / "auth" / "users.json"
    user_settings_dir: Path = DATA / "auth" / "user_settings"
    auto_approve_gateway_jobs: bool = (
        os.getenv("AUTO_APPROVE_GATEWAY_JOBS", "false").lower() == "true"
    )
    audit_log: Path = DATA / "audit.jsonl"
    approved_terms: Path = DATA / "approved_terms.jsonl"

    # Prompt files are static app assets (committed to git), not user data
    llm_export_prompts_dir: Path = ROOT / "data" / "llm_export_prompts"

    def ensure_dirs(self) -> None:
        dirs = [
            self.input_dir, self.extracted_dir, self.anonymized_dir,
            self.reviewed_dir, self.normalized_dir, self.exported_dir,
            self.final_dir, self.jobs_dir, self.mappings_dir,
            self.zone1_previews_dir, self.prepared_packages_dir, self.llm_runs_dir,
            self.source_configs_dir,
            self.ingest_registry_path.parent,  # data/zone1/
            self.gateway_local_dir,
            self.gateway_sftp_dir,
            self.auth_dir,
            self.user_settings_dir,
        ]
        if self.demo_capture:
            dirs += [self.demo_runs_dir]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
