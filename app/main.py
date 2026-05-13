"""Confidoc server entry point."""

from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.review_ui.routes import router

app = FastAPI(title="Confidoc — Secure Document Pipeline")

app.include_router(router)

_static = Path(__file__).parent / "review_ui" / "static"
if _static.exists():
    app.mount("/static", StaticFiles(directory=str(_static)), name="static")


@app.get("/api/health")
def health():
    return {"status": "ok"}


def main() -> None:
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)


if __name__ == "__main__":
    main()
