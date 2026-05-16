# Confidoc — Usage Guide

## The Zone 1 Workflow (step by step)

### Step 1 — Open the app

```
uv run confidoc
```

Open `http://127.0.0.1:8100` in your browser.

---

### Step 2 — Load a PDF

Click **New** in the sidebar.

**Option A — Upload from your machine**
Drag and drop a PDF onto the drop zone, or click it to browse.

**Option B — From the data/input/ folder**
Place PDFs in `data/input/` and select them from the folder picker dropdown.

---

### Step 3 — Choose OCR model

In Step 2 of the wizard, select your provider and model:

| Provider | API key needed | Model example |
|---|---|---|
| Google (direct) | `AIza…` from aistudio.google.com | `gemini-2.0-flash` |
| OpenRouter | `sk-or-v1-…` | `google/gemini-2.0-flash` |
| OpenAI | `sk-…` | `gpt-4o` |
| Localhost | none | your local model name |

Leave all fields blank to use the server-configured defaults (set in `.env`).

---

### Step 4 — Run OCR extraction

Click **Run OCR Extraction**. The app will:
- Render each PDF page to PNG (saved to `data/zone1/previews/{job_id}/`)
- Send pages to the vision LLM for markdown extraction
- Run regex + LLM PII detection passes
- Display the job in the sidebar with status **reviewing**

The Zone 1 red banner confirms you are viewing raw PHI.

---

### Step 5 — Review entities

The job opens in the entity review view.

**View the original PDF side-by-side:**
Click **Compare PDF** in the header to open the PDF panel next to the extracted text.

**Entity actions (left panel):**
- **✓ Approve** — include this entity in the pseudonymized export
- **✗ Dismiss** — exclude it (not replaced, kept in audit record)
- **🗑 Delete** — remove entirely so you can re-annotate the same span

**Add missing entities manually:**
Select any text in the right panel → the label picker appears → choose a label → click **Add**.

Available labels include: `PATIENT_NAME`, `PHYSICIAN_NAME`, `DATE`, `ADDRESS`, `LOCATION`, `CASE_ID`, `ID_NUMBER`, `PHONE`, `EMAIL`, `ORGANIZATION`, `INSURANCE_ID`, `BANK_INFORMATION`, `DIAGNOSIS`, `MEDICATION`, `OTHER_PII`.

**Tip:** Click **Approve All** to approve every pending entity in one step.

---

### Step 6 — Export

Click **Export** (top-right of the detail header). This:
1. Assigns stable numbered tokens to all approved entities (`[PATIENT_NAME_001]`, `[DATE_001]` etc.)
2. Produces the **pseudonymized markdown** (`data/reviewed/`)
3. Saves an **encrypted token mapping** (`data/mappings/`)
4. Writes **TMX** and **CSV** files (`data/exported/`)
5. Job status moves to **done**

After export, click the **🔑 Mapping** tab to verify every token mapped correctly.

---

### Step 7 — Download outputs

In the export bar at the bottom:
- **TMX** — translation memory file (bilingual segments)
- **CSV** — segment pairs in spreadsheet format
- **MD** — raw pseudonymized markdown

---

### Step 8 — Rehydrate (optional)

If you need to restore original values into an anonymized output, use the **Rehydrate** action. This decrypts the mapping file and substitutes tokens back to original values.

---

## Deploying on Render.com

### How it works

Deployment is via **GitHub → Render auto-deploy**:

```
local code  →  git push  →  GitHub repo  →  Render builds Docker image  →  live URL
```

Every `git push` to `main` triggers an automatic redeploy (takes ~3–5 min).

### First-time setup

**1. Push to GitHub**

```bash
git init
git add -A
git commit -m "initial commit"
git remote add origin https://github.com/YOUR_USERNAME/confidoc.git
git push -u origin main
```

**2. Connect Render**

1. Go to [render.com](https://render.com) → **New** → **Web Service**
2. Connect your GitHub account → select the `confidoc` repo
3. Render detects `render.yaml` automatically

**3. Set secret env vars in the Render dashboard**

| Variable | Value |
|---|---|
| `GOOGLE_API_KEY` | Your Gemini API key (`AIza…`) |
| `MAPPING_KEY` | Generate once (see below) |
| `CONFIDOC_ANON_PROVIDER` | `google` |
| `CONFIDOC_ANON_MODEL` | `gemini-2.0-flash` |
| `CONFIDOC_ANON_API_KEY` | Your Gemini API key |
| `CONFIDOC_PDF_PROVIDER` | `google` |
| `CONFIDOC_PDF_MODEL` | `gemini-2.0-flash` |
| `CONFIDOC_PDF_API_KEY` | Your Gemini API key |

Generate `MAPPING_KEY`:
```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**4. Click Create Web Service**

You get a URL like `https://confidoc.onrender.com`. Cost: ~$7.25/month (Starter plan + 1 GB disk).

### Re-deploying after changes

```bash
git add -A
git commit -m "describe change"
git push
```

Render redeploys automatically.

---

## Demonstrating the app with a redacted file

To demo the app without exposing real patient data, use the **Import** feature to load a pre-prepared pseudonymized markdown file:

### Option A — Import a pseudonymized markdown file

1. Prepare a `.md` file with real names already replaced by tokens
   (e.g. use any of the files in `data/reviewed/` from a completed local job)
2. Click **↑ Import artifact** in the sidebar
3. Select stage: `reviewed` (pseudonymized)
4. Upload the `.md` file
5. The job appears in the sidebar at **approved** status — ready to export or send to LLM
6. The original PDF is not present, so the Compare PDF button will 404 (expected for demo imports)

### Option B — Create a synthetic demo PDF

Create a PDF with obviously fake but realistic-looking medical data:

```
Patient: Max Mustermann
DOB: 01.01.1980
Case: DEMO-12345
Diagnosis: Routine check-up
Physician: Dr. Anna Demo
Address: Musterstraße 1, 12345 Musterstadt
```

Process it through the full pipeline locally to verify everything works, then deploy the result (the pseudonymized `.md` and export files only — never the source PDF or mapping).

### What is safe to share / deploy publicly

| Artifact | Safe to share? |
|---|---|
| Pseudonymized markdown (`data/reviewed/`) | ✓ Yes — tokens only, no real PII |
| TMX / CSV exports (`data/exported/`) | ✓ Yes — same |
| Encrypted mapping (`data/mappings/`) | ✗ No — contains real values |
| Original PDF (`data/input/`) | ✗ No — raw PHI |
| Extracted markdown (`data/extracted/`) | ✗ No — raw PHI |
| `MAPPING_KEY` env var | ✗ No — decrypts everything |

---

## Key files and directories

```
data/
  input/          ← place source PDFs here (Zone 1, never committed)
  extracted/      ← raw markdown from OCR (Zone 1)
  reviewed/       ← pseudonymized markdown (safe for Zone 2)
  normalized/     ← OCR-corrected markdown
  exported/       ← TMX and CSV files
  mappings/       ← encrypted token↔original maps (Zone 1)
  zone1/previews/ ← page PNGs for the PDF viewer (Zone 1)
  jobs/           ← job metadata JSON

data/prompts/     ← prompt templates for Send to LLM (coming)
data/zone2/       ← LLM report outputs (coming)
```

---

## Environment variables (`.env`)

```
CONFIDOC_PDF_PROVIDER=google
CONFIDOC_PDF_MODEL=gemini-2.0-flash
CONFIDOC_PDF_API_KEY=AIza...

CONFIDOC_ANON_PROVIDER=google
CONFIDOC_ANON_MODEL=gemini-2.0-flash
CONFIDOC_ANON_API_KEY=AIza...

MAPPING_KEY=<generated fernet key>

CONFIDOC_REDZONE_REMINDER_MINUTES=10   # session reminder interval
CONFIDOC_SESSION_TIMEOUT_HOURS=8       # login session duration (auth coming)
```
