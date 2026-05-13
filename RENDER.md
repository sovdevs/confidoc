# Deploying Confidoc on Render.com

## Step 1 — Push to GitHub

Go to [github.com](https://github.com) → New repository → name it `confidoc` → do **not** add a README. Then:

```bash
git remote add origin https://github.com/YOUR_USERNAME/confidoc.git
git push -u origin main
```

## Step 2 — Deploy on Render

1. Go to [render.com](https://render.com) → **New** → **Web Service**
2. Connect your GitHub account → select the `confidoc` repo
3. Render will detect `render.yaml` automatically and pre-fill the settings
4. Set these two env vars manually in the dashboard (marked `sync: false` for security):

   | Variable | Value |
   |---|---|
   | `GOOGLE_API_KEY` | Your Gemini API key |
   | `MAPPING_KEY` | Generate once locally (see below) |

   To generate `MAPPING_KEY`:
   ```bash
   .venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
   Copy the output and paste it into the Render dashboard. Keep it safe — losing it makes existing encrypted mapping files unreadable.

5. Click **Create Web Service**

Build takes ~3–5 minutes (uv installs deps, no docling ML models to download). You'll get a URL like `https://confidoc.onrender.com`.

## Cost

| Item | Cost |
|---|---|
| Starter plan (512 MB RAM, always-on) | $7.00/month |
| Persistent disk (1 GB, stores all job data) | $0.25/month |
| **Total** | **~$7.25/month** |

The free tier exists but sleeps after 15 minutes of inactivity — use the Starter plan for a demo that needs to stay responsive.

## What changes on the server vs local

**PDF extraction**: PyMuPDF handles extraction instead of docling. Docling's import fails gracefully on the memory-constrained server and the pipeline falls back to PyMuPDF automatically. Gemini still does the markdown conversion, so output quality is the same.

**Data persistence**: All job files, encrypted mappings, exports, and the approved-terms log live on the mounted persistent disk at `/data`. They survive deploys and restarts. The `MAPPING_KEY` env var must stay the same across deploys — changing it makes existing mapping files unreadable.

**Environment variables set by `render.yaml`** (no action needed):

| Variable | Value |
|---|---|
| `DATA_DIR` | `/data` |
| `HOST` | `0.0.0.0` |
| `PORT` | `8100` |
| `GEMINI_MODEL` | `gemini-2.0-flash` |
| `MAX_CONCURRENT_PDFS` | `2` |
| `MAX_CONCURRENT_PAGES` | `4` |

## Re-deploying after code changes

```bash
git add -A
git commit -m "your message"
git push
```

Render auto-deploys on every push to `main`.
