"""Base types and interface for all source connectors."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".rtf", ".txt", ".md", ".odt"}


def extension_info(ext: str) -> tuple[bool, bool, str]:
    """Return (supported, requires_ocr, processor_hint) for a file extension."""
    ext = ext.lower()
    if ext == ".pdf":
        return True, True, "pdf_ocr"
    elif ext in {".docx", ".doc", ".odt", ".rtf"}:
        return True, False, "office_to_markdown"
    elif ext in {".txt", ".md"}:
        return True, False, "text_to_markdown"
    return False, False, "unsupported"


@dataclass
class RemoteFile:
    remote_path: str
    filename: str
    extension: str
    size_bytes: int
    modified_at: str          # ISO 8601 or RFC 2822
    remote_id: str            # path-hash or provider-native file ID
    status: str = "new"       # new | seen | changed | unsupported | error
    supported: bool = True
    requires_ocr: bool = True
    error: Optional[str] = None


@dataclass
class SourceTestResult:
    ok: bool
    message: str


@dataclass
class DownloadResult:
    ok: bool
    local_path: Optional[Path] = None
    error: Optional[str] = None
    size_bytes: int = 0
    content_sha256: str = ""


class SourceConnector:
    """Abstract interface for all source connectors."""

    def __init__(self, config: dict) -> None:
        self.config = config

    def test(self) -> SourceTestResult:
        raise NotImplementedError

    def list_files(self, pattern: str | None = None) -> list[RemoteFile]:
        raise NotImplementedError

    def download_file(self, remote_path: str, destination: Path) -> DownloadResult:
        raise NotImplementedError
