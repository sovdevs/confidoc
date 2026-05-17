"""GitHub private repo source connector using the GitHub REST API."""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import os
from pathlib import Path
from typing import Optional

import httpx

from app.connectors.base import (
    DownloadResult, RemoteFile, SourceConnector, SourceTestResult, extension_info,
)

_GH_API = "https://api.github.com"


def _env(config: dict, key: str) -> Optional[str]:
    env_name = config.get(key)
    return os.environ.get(env_name) if env_name else None


class GitHubConnector(SourceConnector):
    """GitHub repo source. Token resolved from env var."""

    def _headers(self) -> dict:
        token = _env(self.config, "token_env") or self.config.get("token", "")
        h: dict = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    def _repo(self) -> str:
        return self.config.get("repo", "")

    def _branch(self) -> str:
        return self.config.get("branch", "main")

    def _path(self) -> str:
        return self.config.get("remote_path", "").lstrip("/")

    def test(self) -> SourceTestResult:
        try:
            resp = httpx.get(
                f"{_GH_API}/repos/{self._repo()}/contents/{self._path()}",
                params={"ref": self._branch()},
                headers=self._headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                return SourceTestResult(
                    ok=True,
                    message=f"Accessible: {self._repo()}/{self._path()} on {self._branch()}",
                )
            if resp.status_code == 401:
                return SourceTestResult(ok=False, message="Authentication failed — check your token.")
            if resp.status_code == 404:
                return SourceTestResult(ok=False, message="Repository or path not found.")
            return SourceTestResult(ok=False, message=f"GitHub returned {resp.status_code}.")
        except Exception as exc:
            return SourceTestResult(ok=False, message=str(exc)[:200])

    def list_files(self, pattern: str | None = None) -> list[RemoteFile]:
        patterns: list[str] = (
            [p.strip() for p in pattern.split(",")]
            if pattern
            else self.config.get("filename_patterns", ["*"])
        )
        try:
            resp = httpx.get(
                f"{_GH_API}/repos/{self._repo()}/contents/{self._path()}",
                params={"ref": self._branch()},
                headers=self._headers(),
                timeout=15,
            )
            resp.raise_for_status()
            items = resp.json()
        except Exception as exc:
            raise RuntimeError(f"GitHub listing failed: {str(exc)[:200]}")

        results: list[RemoteFile] = []
        for item in items if isinstance(items, list) else []:
            if item.get("type") != "file":
                continue
            name = item["name"]
            if not any(fnmatch.fnmatch(name, p) for p in patterns):
                continue

            ext = Path(name).suffix.lower()
            supported, requires_ocr, _ = extension_info(ext)
            sha = (item.get("sha") or "")[:16]

            results.append(RemoteFile(
                remote_path=item["path"],
                filename=name,
                extension=ext,
                size_bytes=item.get("size", 0),
                modified_at="",  # GitHub contents API does not include mtime
                remote_id=sha,
                supported=supported,
                requires_ocr=requires_ocr,
            ))

        return results

    def download_file(self, remote_path: str, destination: Path) -> DownloadResult:
        try:
            meta = httpx.get(
                f"{_GH_API}/repos/{self._repo()}/contents/{remote_path}",
                params={"ref": self._branch()},
                headers=self._headers(),
                timeout=10,
            ).raise_for_status().json()

            destination.parent.mkdir(parents=True, exist_ok=True)
            h = hashlib.sha256()

            download_url = meta.get("download_url")
            if download_url:
                with httpx.stream("GET", download_url, headers=self._headers(), timeout=60) as r:
                    r.raise_for_status()
                    with open(destination, "wb") as f:
                        for chunk in r.iter_bytes(chunk_size=65536):
                            f.write(chunk)
                            h.update(chunk)
            else:
                # Fall back to base64-encoded content in the metadata response
                data = base64.b64decode(meta.get("content", "").replace("\n", ""))
                destination.write_bytes(data)
                h.update(data)

            size = destination.stat().st_size
            return DownloadResult(
                ok=True, local_path=destination,
                size_bytes=size, content_sha256=h.hexdigest(),
            )
        except Exception as exc:
            return DownloadResult(ok=False, error=str(exc)[:200])
