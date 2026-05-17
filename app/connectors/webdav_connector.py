"""WebDAV / Nextcloud source connector using httpx."""

from __future__ import annotations

import fnmatch
import hashlib
import os
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

import httpx

from app.connectors.base import (
    DownloadResult, RemoteFile, SourceConnector, SourceTestResult, extension_info,
)


def _env(config: dict, key: str) -> Optional[str]:
    env_name = config.get(key)
    return os.environ.get(env_name) if env_name else None


class WebDAVConnector(SourceConnector):
    """WebDAV / Nextcloud source. Credentials resolved from env vars."""

    _PROPFIND_BODY = (
        b'<?xml version="1.0"?>'
        b'<propfind xmlns="DAV:"><prop>'
        b'<getcontentlength/><getlastmodified/><resourcetype/>'
        b'</prop></propfind>'
    )

    def _base_url(self) -> str:
        url = _env(self.config, "base_url_env") or self.config.get("base_url", "")
        return url.rstrip("/")

    def _auth(self) -> tuple[str, str]:
        user = _env(self.config, "username_env") or self.config.get("username", "")
        pwd  = _env(self.config, "password_env")  or self.config.get("password",  "")
        return user, pwd

    def _remote_url(self) -> str:
        return self._base_url() + self.config.get("remote_path", "/")

    def test(self) -> SourceTestResult:
        try:
            resp = httpx.request(
                "PROPFIND", self._remote_url(),
                auth=self._auth(),
                headers={"Depth": "0"},
                timeout=10,
            )
            if resp.status_code in (200, 207):
                return SourceTestResult(ok=True, message="Connection successful.")
            return SourceTestResult(ok=False, message=f"Server returned {resp.status_code}.")
        except Exception as exc:
            return SourceTestResult(ok=False, message=str(exc)[:200])

    def list_files(self, pattern: str | None = None) -> list[RemoteFile]:
        patterns: list[str] = (
            [p.strip() for p in pattern.split(",")]
            if pattern
            else self.config.get("filename_patterns", ["*"])
        )
        remote_dir = self.config.get("remote_path", "/")

        try:
            resp = httpx.request(
                "PROPFIND", self._remote_url(),
                auth=self._auth(),
                headers={"Depth": "1", "Content-Type": "application/xml"},
                content=self._PROPFIND_BODY,
                timeout=20,
            )
            resp.raise_for_status()
        except Exception as exc:
            raise RuntimeError(f"WebDAV listing failed: {str(exc)[:200]}")

        ns = {"d": "DAV:"}
        root = ET.fromstring(resp.text)
        results: list[RemoteFile] = []

        for response in root.findall(".//d:response", ns):
            href = (response.findtext("d:href", namespaces=ns) or "").rstrip("/")
            if href.endswith(remote_dir.rstrip("/")):
                continue  # skip the directory itself

            # Check it's a file (no resourcetype/collection)
            rtype = response.find(".//d:resourcetype/d:collection", ns)
            if rtype is not None:
                continue

            name = href.rsplit("/", 1)[-1]
            if not any(fnmatch.fnmatch(name, p) for p in patterns):
                continue

            ext = Path(name).suffix.lower()
            supported, requires_ocr, _ = extension_info(ext)
            size = int(response.findtext(".//d:getcontentlength", namespaces=ns) or 0)
            mtime = response.findtext(".//d:getlastmodified", namespaces=ns) or ""
            remote_id = hashlib.sha256(href.encode()).hexdigest()[:16]

            results.append(RemoteFile(
                remote_path=href,
                filename=name,
                extension=ext,
                size_bytes=size,
                modified_at=mtime,
                remote_id=remote_id,
                supported=supported,
                requires_ocr=requires_ocr,
            ))

        return results

    def download_file(self, remote_path: str, destination: Path) -> DownloadResult:
        url = self._base_url() + remote_path
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            h = hashlib.sha256()
            with httpx.stream("GET", url, auth=self._auth(), timeout=60) as r:
                r.raise_for_status()
                with open(destination, "wb") as f:
                    for chunk in r.iter_bytes(chunk_size=65536):
                        f.write(chunk)
                        h.update(chunk)
            size = destination.stat().st_size
            return DownloadResult(
                ok=True, local_path=destination,
                size_bytes=size, content_sha256=h.hexdigest(),
            )
        except Exception as exc:
            return DownloadResult(ok=False, error=str(exc)[:200])
