FROM python:3.12-slim

# uv for fast, reproducible installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Install deps first (cached layer — only rebuilds when pyproject.toml/uv.lock change)
COPY pyproject.toml uv.lock ./
COPY vendor/ vendor/
RUN uv sync --no-dev --frozen

# App code
COPY app/ app/

# Static demo inputs and pre-captured playback artifacts (synthetic only — no real PHI)
COPY data/demo/ data/demo/
COPY data/demo_runs/ data/demo_runs/

# Render mounts a persistent disk at /data via the DATA_DIR env var
ENV DATA_DIR=/data
ENV HOST=0.0.0.0
ENV PORT=8100

EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8100/api/health')"

CMD ["uv", "run", "confidoc"]
