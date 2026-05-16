"""Smoke tests for the BYOK vision extension and Confidoc integration.

Three groups:
  1. test_byok_text_request_still_works          — tmx-dump compatibility
  2. test_llmrequest_from_vision_*               — vision API shape
  3. test_ingest_scanned_pdf_uses_byok_vision    — full ingest path
"""

import asyncio
import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cogtrix_byok import LLMRequest, LLMResponse, ProviderName


# ── Group 1: text-only BYOK API — must remain unchanged for tmx-dump ─────────

def test_byok_text_request_still_works():
    """Constructing LLMRequest with plain string content must work exactly as before."""
    r = LLMRequest(
        messages=[
            {"role": "system", "content": "You are helpful."},
            {"role": "user",   "content": "Translate: Hello"},
        ],
        model="gpt-4o-mini",
        temperature=0.0,
    )
    assert r.messages[0]["role"] == "system"
    assert r.messages[1]["content"] == "Translate: Hello"
    assert r.temperature == 0.0
    assert r.max_tokens is None
    assert r.response_format is None


def test_byok_text_request_with_response_format():
    """response_format kwarg still accepted unchanged."""
    r = LLMRequest(
        messages=[{"role": "user", "content": "Return JSON"}],
        model="gpt-4o",
        response_format={"type": "json_object"},
    )
    assert r.response_format == {"type": "json_object"}


def test_supports_vision_flags():
    """provider-level supports_vision markers are correct."""
    from cogtrix_byok.providers.openai_provider     import OpenAIProvider
    from cogtrix_byok.providers.openrouter_provider import OpenRouterProvider
    from cogtrix_byok.providers.localhost_provider  import LocalhostProvider
    from cogtrix_byok.providers.anthropic_provider  import AnthropicProvider

    assert OpenAIProvider.supports_vision     is True
    assert OpenRouterProvider.supports_vision is True
    assert LocalhostProvider.supports_vision  is True
    assert AnthropicProvider.supports_vision  is False   # stub, not implemented


# ── Group 2: LLMRequest.from_vision message structure ─────────────────────────

_FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50


def test_llmrequest_from_vision_builds_openai_compatible_message():
    """from_vision() produces the OpenAI/OpenRouter multimodal message format."""
    r = LLMRequest.from_vision(
        text="Convert this page to Markdown.",
        images=[_FAKE_PNG],
        model="google/gemini-2.0-flash",
        system="You are a medical document extractor.",
    )

    assert r.model == "google/gemini-2.0-flash"
    assert r.temperature == 0.0
    assert len(r.messages) == 2                          # system + user

    system_msg = r.messages[0]
    assert system_msg["role"] == "system"
    assert "medical document extractor" in system_msg["content"]

    user_msg = r.messages[1]
    assert user_msg["role"] == "user"
    assert isinstance(user_msg["content"], list)

    parts = user_msg["content"]
    assert parts[0] == {"type": "text", "text": "Convert this page to Markdown."}

    img_part = parts[1]
    assert img_part["type"] == "image_url"
    url = img_part["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",")[1]) == _FAKE_PNG


def test_llmrequest_from_vision_no_system():
    """from_vision() without system produces a single user message."""
    r = LLMRequest.from_vision(
        text="Describe.",
        images=[_FAKE_PNG],
        model="gpt-4o",
    )
    assert len(r.messages) == 1
    assert r.messages[0]["role"] == "user"


def test_llmrequest_from_vision_multiple_images():
    """Multiple images are encoded as separate image_url parts."""
    r = LLMRequest.from_vision(
        text="Compare pages.",
        images=[_FAKE_PNG, _FAKE_PNG],
        model="gpt-4o",
    )
    parts = r.messages[0]["content"]   # no system → user is first
    assert parts[0]["type"] == "text"
    assert parts[1]["type"] == "image_url"
    assert parts[2]["type"] == "image_url"
    assert len(parts) == 3


def test_llmrequest_from_vision_kwargs_forwarded():
    """temperature and max_tokens are forwarded."""
    r = LLMRequest.from_vision(
        text="x", images=[_FAKE_PNG], model="gpt-4o",
        temperature=0.3, max_tokens=512,
    )
    assert r.temperature == 0.3
    assert r.max_tokens == 512


# ── Group 3: ingest scanned PDF uses BYOK vision path ─────────────────────────

@pytest.mark.asyncio
async def test_ingest_scanned_pdf_uses_byok_vision(tmp_path):
    """
    End-to-end ingest path for a scanned (image-only) PDF:
      upload → PyMuPDF render → BYOK vision call → markdown saved.

    PyMuPDF and the BYOK adapter are mocked so no real files or API calls occur.
    """
    from app.storage.jobs import Job, JobStatus
    from app.pipeline import ingest

    job = Job(filename="test_scan.pdf")

    # Minimal fake PDF bytes (not a real PDF — PyMuPDF is mocked)
    fake_pdf_bytes = b"%PDF-1.4 fake"

    fake_markdown = "# Test Report\n\nPatient: John Doe\n\nDiagnosis: healthy."

    # Mock PyMuPDF: one page, renders to fake PNG bytes
    fake_pixmap = MagicMock()
    fake_pixmap.tobytes.return_value = _FAKE_PNG

    fake_page = MagicMock()
    fake_page.get_pixmap.return_value = fake_pixmap

    fake_doc = MagicMock()
    fake_doc.__enter__ = MagicMock(return_value=fake_doc)
    fake_doc.__exit__ = MagicMock(return_value=False)
    fake_doc.__len__ = MagicMock(return_value=1)
    fake_doc.__iter__ = MagicMock(return_value=iter([fake_page]))

    # Mock job_store so we don't need real files
    with (
        patch("app.pipeline.ingest.job_store") as mock_job_store,
        patch("app.pipeline.ingest.audit_log"),
        patch("app.pipeline.ingest.settings") as mock_settings,
        patch("app.services.llm_adapter.pdf_complete_vision", new_callable=AsyncMock) as mock_llm,
        patch("fitz.open", return_value=fake_doc),
    ):
        mock_settings.input_dir    = tmp_path
        mock_settings.extracted_dir = tmp_path
        mock_settings.jobs_dir     = tmp_path / "jobs"
        mock_settings.max_concurrent_pages = 2
        mock_settings.pdf_provider = "openrouter"
        mock_settings.pdf_model    = "google/gemini-2.0-flash"

        mock_llm.return_value = fake_markdown

        loaded_job = Job(filename="test_scan.pdf", status=JobStatus.reviewing,
                         extracted_md="extracted/test_scan.md")
        mock_job_store.load.return_value = loaded_job
        mock_job_store.update_status.return_value = loaded_job

        result = await ingest.run(job, fake_pdf_bytes)

    # BYOK vision was called with the rendered image
    mock_llm.assert_called_once()
    call_kwargs = mock_llm.call_args
    assert call_kwargs.kwargs["images"] == [_FAKE_PNG]
    assert "medical" in call_kwargs.kwargs["system"].lower()
    assert "do not anonymize" in call_kwargs.kwargs["system"].lower()

    # PyMuPDF rendered at 2× scale
    fake_page.get_pixmap.assert_called_once()
    matrix_arg = fake_page.get_pixmap.call_args.kwargs.get("matrix") or \
                 fake_page.get_pixmap.call_args.args[0]
    # Matrix(2.0, 2.0) → a=2.0, d=2.0
    assert matrix_arg.a == pytest.approx(2.0)

    # Markdown was written to disk
    written = (tmp_path / "test_scan.md")
    assert written.exists()
    assert "Test Report" in written.read_text()
