# Confidoc — Developer Notes

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) — `brew install uv`
- An OpenRouter API key (or GOOGLE_API_KEY as fallback)
- The `cogtrix_byok` package must be present at `../../../prog/PY/tmx-dump/cogtrix_byok`
  relative to this repo (see `pyproject.toml`)

## Local setup

```bash
git clone <repo>
cd confidoc
uv sync                  # installs all deps including cogtrix_byok and vendor/pdf-to-markdown
cp .env.example .env     # then fill in your keys
uv run confidoc          # starts at http://127.0.0.1:8100
```

### Minimal `.env`

```bash
OPENROUTER_API_KEY=sk-or-...
MAPPING_KEY=<run: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
```

## Running tests

```bash
uv run pytest                          # all tests
uv run pytest tests/test_byok_integration.py -v   # BYOK smoke tests
uv run pytest tests/policy_engine/ -v  # Policy Engine tests
```

## Key architecture

```
app/
  config.py              settings + LLM profiles (pdf / anon)
  main.py                FastAPI app, mounts router + static
  pipeline/
    ingest.py            PDF → pages → BYOK vision → markdown
    anon.py              regex PII detection
    anon_llm.py          LLM PII detection (BYOK, text-only)
    export.py            stable token assignment + TMX/CSV export
    ocr_check.py         OCR artefact detection for normalization stage
  api/
    server_sources.py    source ingest endpoints (list/test/pull)
  connectors/
    base.py              SourceConnector interface + RemoteFile types
    sftp_connector.py    SFTP via paramiko (key or password auth from env)
    webdav_connector.py  WebDAV/Nextcloud via httpx PROPFIND
    github_connector.py  GitHub REST API (private repo file listing/download)
  services/
    llm_adapter.py       thin BYOK wrapper (pdf_complete_vision / anon_complete / llm_export_complete)
    prompt_loader.py     load LLM export prompts from data/llm_export_prompts/*.md
    source_config_loader.py  load data/source_configs/sources.json; strip credentials
    ingest_registry.py   append-only JSONL tracking seen remote files
    source_ingest_service.py  download → sanitise → create imported job → register
  review_ui/
    routes.py            all FastAPI routes (including /api/jobs/{id}/process)
    templates/index.html single-page browser UI
  storage/
    jobs.py              Job model + file-backed job store
    mappings.py          Fernet-encrypted token mapping (assign_tokens / rehydrate)
    audit_log.py         append-only JSONL event log

vendor/
  pdf-to-markdown/       vendored export utilities only (md_to_segments, write_tmx, write_csv)
                         LLM pipeline bypassed — Confidoc owns extraction via BYOK

data/
  input/                 raw PDFs (uploaded or pulled from server sources)
  extracted/             Gemini-extracted markdown (Zone 1 — raw PHI)
  reviewed/              pseudonymized markdown (stable tokens applied)
  normalized/            OCRCheck-corrected markdown
  exported/              TMX + CSV (Zone 2 artifacts)
  final/                 rehydrated final documents (Zone 1, controlled)
  jobs/                  one JSON per job
  mappings/              encrypted token maps — never exported
  prepared_packages/     policy engine output packages (Zone 2)
  llm_runs/              LLM export run artifacts (per job_id)
  llm_export_prompts/    saved prompt .md files for LLM export feature
  source_configs/        sources.json (operator-managed, see sources.sample.json)
  gateway/
    local/
      incoming/          drop files here for gateway pickup
      processing/        file moves here while its job is running
      processed/         renamed {job_id}_{filename} on success
      failed/            renamed {timestamp}_{filename} on error
      exports/           {job_id}/ per-job export artifacts (auto mode)
      registry.jsonl     append-only event log for gateway activity
      batch_status.json  live progress for the current Process All batch
  zone1/
    previews/            per-job PDF page PNGs (Zone 1 only)
    ingest_registry.jsonl  seen-file registry for server source deduplication
  audit.jsonl            append-only event log
  approved_terms.jsonl   LLM few-shot knowledge base
```

## LLM provider configuration

Two profiles, each independently configurable — see `docs/byok_confidoc_integration.md`
for the full reference.

| Profile | What it does | Default model |
|---|---|---|
| PDF | Scanned page images → Markdown | `google/gemini-2.0-flash` via OpenRouter |
| ANON | PII detection on masked text | `google/gemini-2.0-flash` via OpenRouter |

Switch to a local model:
```bash
CONFIDOC_PDF_PROVIDER=localhost
CONFIDOC_PDF_MODEL=llava:13b
CONFIDOC_PDF_BASE_URL=http://localhost:11434/v1
```

When using the Google direct provider (not via OpenRouter), model IDs must omit the
`google/` prefix — e.g. `gemini-2.0-flash`, not `google/gemini-2.0-flash`. The LLM
adapter strips the prefix automatically when `provider=google`.

## LLM Export

Users can send the approved pseudonymized markdown to any configured LLM from the Export tab.

Saved prompts live in `data/llm_export_prompts/*.md` (YAML front matter optional).
Run artifacts are stored in `data/llm_runs/{job_id}/{run_id}.json` — no API keys, no PHI.

Adding a prompt: drop a `.md` file into `data/llm_export_prompts/` and restart (or add a
`COPY` line to the Dockerfile for deployed instances).

## Server Source Ingest

The Server tab in Step 1 of the upload wizard lets operators pull documents from remote
sources into Zone 1 without triggering processing.

**Job lifecycle:**
```
imported → (user clicks Process) → processing → extracting → reviewing → …
```

`imported` and `processing` are distinct statuses — the pipeline never starts automatically
on server-ingested files.

**Configure sources:** copy `data/source_configs/sources.sample.json` to
`data/source_configs/sources.json` and fill in real values. Credentials are referenced via
env var names only — never stored in the config file itself.

```json
{
  "id": "clinic_sftp",
  "type": "sftp",
  "host": "sftp.example.com",
  "username_env": "CONFIDOC_SFTP_USER",
  "private_key_path_env": "CONFIDOC_SFTP_KEY_PATH",
  "remote_path": "/incoming/reports",
  "filename_patterns": ["*.pdf", "*.docx"],
  "enabled": true
}
```

Supported connector types: `sftp`, `webdav` / `nextcloud`, `github`.

**Deduplication:** `data/zone1/ingest_registry.jsonl` tracks every pulled file by
`(source_id, remote_path, size, mtime)`. Files are classified as `new`, `seen`, or
`changed`. Changed files are imported as a new job; the registry records the
`previous_job_id` link.

**Security rules:**
- Credentials resolved from env vars at connect time; never logged or stored in artifacts
- Remote paths hashed in audit events (filenames may contain PHI)
- Filenames sanitised before writing to disk (path traversal prevention)
- Supported extensions enforced: `.pdf .docx .doc .rtf .txt .md .odt`
- Non-PDF imports get `requires_ocr=false`; the Process button is disabled for them
  pending future extraction support

## Local Folder Gateway (Secure Gateway Phase 1)

The gateway is a local-folder intake channel that reuses the existing Confidoc pipeline.
No separate processing logic is built — it calls `ingest.run()`, `anon.run()`,
`anon_llm.run()`, and `export.run()` directly.

**Demo flow:**
```bash
mkdir -p data/gateway/local/incoming
cp /path/to/reports/*.pdf data/gateway/local/incoming/
# Start Confidoc, then: Server tab → Local Folder → Scan → Process All
```

**Two processing modes:**

| Button | Behaviour | Auto-approve? |
|--------|-----------|---------------|
| **Process Next** | Foreground — blocks until one file completes, returns result | Respects `AUTO_APPROVE_GATEWAY_JOBS` |
| **Process All** | Background batch — returns immediately, jobs appear in sidebar as each finishes | Always manual (force_manual=True) |

**Process All** always lands jobs in `reviewing` status regardless of `AUTO_APPROVE_GATEWAY_JOBS`.
This is intentional — batch intake is for ingestion, not automated approval.

**Process Next** with `AUTO_APPROVE_GATEWAY_JOBS=true`:
- OCR → entity detection → auto-approve all entities → `export.run()` → copy
  reviewed MD / TMX / CSV to `exports/{job_id}/` → job marked `done`

**Process Next** with `AUTO_APPROVE_GATEWAY_JOBS=false` (default):
- OCR → entity detection → stops at `reviewing` → job enters normal review queue

**Batch progress:** UI polls `/api/gateway/local/batch-status` every 2.5s and calls
`loadJobs()` on each tick, so jobs populate the sidebar live.

**File lifecycle:**
```
incoming/{file}
  → processing/{file}        (while pipeline runs)
  → processed/{job_id}_{file}  (on success)
  → failed/{timestamp}_{file}  (on error)
```

**`reviewed_md` is created on Approve All** (not on first export). After clicking
Approve All, the pseudonymized markdown with stable tokens is generated immediately,
making OCR Check and LLM Export available without needing to run the policy engine first.

**Gateway endpoints:**
```
GET  /api/gateway/local/status       counts + recent registry events
POST /api/gateway/local/scan         list incoming/ files
POST /api/gateway/local/process-next foreground: process one file
POST /api/gateway/local/process-all  background batch: process all files
GET  /api/gateway/local/batch-status current batch progress
```

**Env var:**
```bash
AUTO_APPROVE_GATEWAY_JOBS=false   # true = auto mode for Process Next only
```

**Known limitations (Phase 1):**
- Local folder only; SFTP/WebDAV/GitHub connectors pull to `data/input/`, not the gateway
- Non-PDF files are ingested (file moves to processing/) but pipeline does not run —
  Process button is disabled; file will move to failed/ with a clear message
- No filesystem watcher; scanning is always manual (click Scan)
- Auto mode is not recommended for fax/low-quality scans — entity offsets may be
  incorrect, and missed PHI will not be caught without human review

## Zone model

- **Zone 1**: everything before export — raw PDF, extracted MD, entity list, mapping
- **Zone 2**: pseudonymized outputs handed to translators/LLMs — TMX, CSV, normalized MD
- **Zone 3**: rehydration — mapping + authorized user → final document

The mapping file (`data/mappings/{job_id}.enc`) never leaves Zone 1.

## Shared dependencies

`cogtrix_byok` is a **shared Cogtrix module**, currently co-located with tmx-dump.
Do not fork it into Confidoc. Do not add Confidoc-specific logic into it.

Future move: will be extracted to its own repository. The adapter (`llm_adapter.py`)
insulates Confidoc from that path change.

## Adding a new pipeline stage

1. Add the new status to `JobStatus` in `app/storage/jobs.py`
2. Add any new artifact paths to `Job` model and `Settings.ensure_dirs()`
3. Add routes in `app/review_ui/routes.py` (or a new router under `app/api/`)
4. Update the UI in `app/review_ui/templates/index.html`
5. Add audit events for the new stage
6. Update `IMPLEMENTATION.md`

## Adding a new source connector

1. Create `app/connectors/{type}_connector.py` extending `SourceConnector`
2. Implement `test()`, `list_files()`, `download_file()`
3. Register the type in `app/connectors/__init__.py → get_connector()`
4. Add an example entry to `data/source_configs/sources.sample.json`
5. Document required env vars in this file

## Auth setup

Confidoc uses session-based auth (HTTP-only cookie, bcrypt passwords).
Auth is enabled by default (`CONFIDOC_AUTH_ENABLED=true`).

### Create a user (required before first run)

```bash
uv run confidoc-auth create-user <username>
# prompts for password (min 8 chars)
```

Other commands:
```bash
uv run confidoc-auth list-users
uv run confidoc-auth delete-user <username>
```

### Security rules

- **Never commit `data/auth/users.json`** — it is gitignored. Create users after deploy.
- **Never commit `data/auth/user_settings/`** — Fernet-encrypted secrets, also gitignored.
- The startup log warns if no users exist or if a demo username (`admin`, `test`, `demo`)
  is detected.

### Required env vars

```bash
MAPPING_KEY=<fernet-key>   # encrypts token maps + user settings
# Optional override for user settings (falls back to MAPPING_KEY):
SETTINGS_KEY=<fernet-key>
```

Generate a Fernet key:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Render deployment

**Option A — `CONFIDOC_DEMO_USERS` env var (easiest for demo):**

Add to Render secret file:
```
CONFIDOC_DEMO_USERS=alice:password123,bob:password456
```
Users are created on first startup if they don't exist. Existing users are never
overwritten, so it's safe to leave this set permanently. Passwords are visible in
the Render dashboard — use strong passwords and treat this as demo-only.

**Option B — one-off shell (recommended for production):**
```bash
# In Render Shell tab after deploy:
uv run confidoc-auth create-user <username> --password <password>
```
Users are persisted to the disk mount at `/data/auth/users.json`.
Requires a persistent disk mounted at `/data`.

**Option C — interactive CLI (local only):**
```bash
uv run confidoc-auth create-user <username>
# prompts for password
```

`CONFIDOC_AUTH_ENABLED=false` disables the middleware entirely (local dev only).

### Render env vars for SFTP gateway

```
CONFIDOC_SFTP_USER=confidoc
CONFIDOC_SFTP_KEY=-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAA...
-----END OPENSSH PRIVATE KEY-----
```

Render secret files support multiline values — paste the full PEM block as-is.
The `sources.json` entry references it via `"private_key_content_env": "CONFIDOC_SFTP_KEY"`.

> **Avoid `private_key_path_env`** on Render — it requires a key file on disk, which
> doesn't survive restarts without a persistent disk. Key content in an env var is safer.

## Deployment

See `RENDER.md` for Render.com instructions.

Key difference from local: `docling` is not installed on the server — PyMuPDF
handles PDF rendering (always the case with the BYOK path). The vision LLM does
the text extraction.

---

## TODO — Future BYOK cleanup

- [ ] Move `cogtrix_byok` out of `tmx-dump` into its own shared Cogtrix package directory/repo
- [ ] Replace the awkward relative path dep (`../../../prog/PY/tmx-dump/cogtrix_byok`) with a clean local or Git reference
- [ ] Add package versioning — e.g. `0.2.0` = vision-enabled baseline (current state)
- [ ] Add native Gemini and Anthropic provider implementations only when actually needed (stubs are fine until then)
- [ ] Keep provider-level `supports_vision` for now — model-level capability registry is optional and can come later if routing logic requires it
