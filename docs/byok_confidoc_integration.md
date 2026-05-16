# BYOK × Confidoc Integration

## Why BYOK is a shared Cogtrix module

`cogtrix_byok` is not Confidoc-specific. It is the LLM provider abstraction layer
for the entire Cogtrix product family — the same package is used by tmx-dump and will
be used by future Cogtrix products.

**Current location:** `../../../prog/PY/tmx-dump/cogtrix_byok` (relative to this repo).
This is a temporary co-location with tmx-dump. The package is conceptually standalone
and will eventually move to its own repository/directory.

**Rule:** Do not fork BYOK into Confidoc. Do not add Confidoc-specific logic into BYOK.
The split is:
- `cogtrix_byok` = reusable provider capability (text + vision)
- `confidoc` = PHI workflow, Zone 1 enforcement, medical prompts, profiles

---

## LLM profiles in Confidoc

Confidoc uses two separate LLM profiles, each independently configurable:

| Profile | Purpose | Key env vars |
|---|---|---|
| **PDF** | Scanned page images → Markdown (vision-capable model required) | `CONFIDOC_PDF_PROVIDER`, `CONFIDOC_PDF_MODEL`, `CONFIDOC_PDF_API_KEY` |
| **ANON** | LLM-assisted PII detection on partially-masked text (text-only) | `CONFIDOC_ANON_PROVIDER`, `CONFIDOC_ANON_MODEL`, `CONFIDOC_ANON_API_KEY` |

Both profiles default to **OpenRouter → `google/gemini-2.0-flash`** using the
`OPENROUTER_API_KEY` env var (or `GOOGLE_API_KEY` as a fallback).

---

## Full environment variable reference

| Variable | Default | Notes |
|---|---|---|
| `OPENROUTER_API_KEY` | — | Primary key for both profiles when using OpenRouter |
| `GOOGLE_API_KEY` | — | Legacy fallback; still accepted |
| `CONFIDOC_PDF_PROVIDER` | `openrouter` | `openrouter` / `openai` / `localhost` |
| `CONFIDOC_PDF_MODEL` | `google/gemini-2.0-flash` | Must be vision-capable |
| `CONFIDOC_PDF_API_KEY` | (falls back to `OPENROUTER_API_KEY`) | |
| `CONFIDOC_PDF_BASE_URL` | `http://localhost:1234/v1` | Only used when provider=localhost |
| `CONFIDOC_ANON_PROVIDER` | `openrouter` | `openrouter` / `openai` / `localhost` |
| `CONFIDOC_ANON_MODEL` | `google/gemini-2.0-flash` | Text-only models work here |
| `CONFIDOC_ANON_API_KEY` | (falls back to `OPENROUTER_API_KEY`) | |
| `CONFIDOC_ANON_BASE_URL` | `http://localhost:1234/v1` | Only used when provider=localhost |

### Minimal `.env` for local development

```bash
OPENROUTER_API_KEY=sk-or-...
MAPPING_KEY=<generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
```

---

## Scanned PDF pipeline

```
User uploads PDF
      │
      ▼
ingest.run()
  ├── saves PDF to data/input/
  └── _extract_pages()
        │
        ├── fitz.open(pdf_path)          — PyMuPDF opens the PDF
        │
        └── for each page (concurrent, max_concurrent_pages):
              │
              ├── page.get_pixmap(Matrix(2.0, 2.0))
              │   └── tobytes("png")     — 144 DPI PNG image
              │
              └── llm_adapter.pdf_complete_vision(
                      images=[png_bytes],
                      text_prompt="Page N of M. Convert...",
                      system=MEDICAL_EXTRACTION_SYSTEM_PROMPT,
                  )
                  └── LLMRequest.from_vision(...)
                      └── _pdf_service.complete(request)
                          └── OpenRouterProvider → OpenRouter API
                              → google/gemini-2.0-flash (vision)
                              → Markdown string

assembled markdown → data/extracted/{stem}.md
      │
      ▼
anon.run()         — regex PII detection
      │
      ▼
anon_llm.run()     — LLM PII detection on masked text (ANON profile)
      │
      ▼
status → reviewing  →  HITL review  →  export  →  Zone 2 package
```

**Why this replaces pdf_to_markdown's internal LLM:**
The original library used `google.genai` directly with a prompt tuned for Italian urban
planning documents. Confidoc needs medical-document prompts, BYOK provider routing,
and correct scanned-PDF handling. The library's export utilities (`md_to_segments`,
`write_tmx`, `write_csv`) are still used unchanged.

---

## What remains in Zone 1

Everything in the pipeline before the Zone 2 export is Zone 1:

| Artifact | Location | Zone |
|---|---|---|
| Original PDF | `data/input/` | Zone 1 |
| Extracted Markdown (raw PHI) | `data/extracted/` | Zone 1 |
| Job state (entity list, status) | `data/jobs/` | Zone 1 |
| Encrypted token mapping | `data/mappings/{job_id}.enc` | Zone 1 — never exported |
| Pseudonymized Markdown | `data/reviewed/` | Zone 1 (output for Zone 2) |
| Normalized Markdown | `data/normalized/` | Zone 1 (output for Zone 2) |
| Exported TMX/CSV | `data/exported/` | Zone 2 artifacts — pseudonymized |
| Final rehydrated document | `data/final/` | Zone 1 — controlled access only |

The LLM in the PDF profile receives raw page images from Zone 1.
This is unavoidable — vision extraction requires reading the document.
The privacy boundary is enforced by Zone 1 access control, not by masking.

The LLM in the ANON profile receives only the **partially-masked** document
(already-detected PII replaced with tokens). The exposure is limited to novel PII
the regex did not catch.

---

## Why `google-genai` was removed

The `google-genai` SDK was previously used directly in:
- `app/pipeline/anon_llm.py` — now uses `llm_adapter.anon_complete()`
- `vendor/pdf-to-markdown/src/.../llm.py` — now bypassed entirely

Removing it:
- Eliminates the hard-wiring to a single provider/SDK
- Makes provider choice runtime-configurable via env vars
- Lets the PDF extraction use any vision-capable model on any supported provider
- Reduces the vendor bundle (one less heavy SDK)

`google-genai` can still be reached via OpenRouter (`provider=openrouter`,
`model=google/gemini-2.0-flash`) — no capability is lost.

---

## Adding a new provider

BYOK supports this without Confidoc changes:

1. Add provider to `cogtrix_byok` following the existing pattern
2. Set `CONFIDOC_PDF_PROVIDER=myprovider` (and/or `ANON`)
3. If the provider is not in the BYOK registry (like `localhost`), the adapter
   handles it via direct instantiation

---

## Future: standalone `cogtrix_byok` package

When `cogtrix_byok` moves to its own repository:

1. Update `pyproject.toml` to point to the new location
2. Replace `{ path = "..." }` with `{ git = "https://github.com/cogtrix/byok" }` or a PyPI ref
3. No changes to `app/services/llm_adapter.py` or any Confidoc code

The adapter layer (`llm_adapter.py`) insulates all of Confidoc from that path change.
