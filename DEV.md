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
  services/
    llm_adapter.py       thin BYOK wrapper (pdf_complete_vision / anon_complete)
  review_ui/
    routes.py            all FastAPI routes
    templates/index.html single-page browser UI
  storage/
    jobs.py              Job model + file-backed job store
    mappings.py          Fernet-encrypted token mapping (assign_tokens / rehydrate)
    audit_log.py         append-only JSONL event log

vendor/
  pdf-to-markdown/       vendored export utilities only (md_to_segments, write_tmx, write_csv)
                         LLM pipeline bypassed — Confidoc owns extraction via BYOK

data/
  input/       raw PDFs
  extracted/   Gemini-extracted markdown (Zone 1 — raw PHI)
  reviewed/    pseudonymized markdown (stable tokens applied)
  normalized/  OCRCheck-corrected markdown
  exported/    TMX + CSV (Zone 2 artifacts)
  final/       rehydrated final documents (Zone 1, controlled)
  jobs/        one JSON per job
  mappings/    encrypted token maps — never exported
  audit.jsonl  append-only event log
  approved_terms.jsonl  LLM few-shot knowledge base
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
3. Add routes in `app/review_ui/routes.py`
4. Update the UI in `app/review_ui/templates/index.html`
5. Add audit events for the new stage
6. Update `IMPLEMENTATION.md`

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
