"""SFTP source connector using paramiko."""

from __future__ import annotations

import fnmatch
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.connectors.base import (
    DownloadResult, RemoteFile, SourceConnector, SourceTestResult, extension_info,
)


def _env(config: dict, key: str) -> Optional[str]:
    env_name = config.get(key)
    return os.environ.get(env_name) if env_name else None


def _safe_err(exc: Exception) -> str:
    """Sanitise error message — never leak credentials or key material."""
    msg = str(exc)
    low = msg.lower()
    if any(w in low for w in ("password", "private key", "token", "authentication failed")):
        return "Authentication failed"
    return msg[:200]


class SFTPConnector(SourceConnector):
    """SFTP/SSH source. Credentials resolved from env vars at connect time."""

    def _connect(self):
        try:
            import paramiko
        except ImportError:
            raise RuntimeError(
                "paramiko is required for SFTP sources. "
                "Install it with: pip install paramiko"
            )

        cfg = self.config
        host = cfg["host"]
        port = int(cfg.get("port", 22))
        username = _env(cfg, "username_env") or cfg.get("username", "")

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        key_path = _env(cfg, "private_key_path_env") or cfg.get("private_key_path")
        password = _env(cfg, "password_env")

        kwargs: dict = dict(hostname=host, port=port, username=username, timeout=15)
        if key_path:
            kwargs["key_filename"] = key_path
        elif password:
            kwargs["password"] = password

        client.connect(**kwargs)
        return client

    def test(self) -> SourceTestResult:
        try:
            client = self._connect()
            sftp = client.open_sftp()
            remote_path = self.config.get("remote_path", "/")
            sftp.stat(remote_path)
            sftp.close()
            client.close()
            return SourceTestResult(
                ok=True,
                message=f"Connection successful. Path '{remote_path}' is accessible.",
            )
        except Exception as exc:
            return SourceTestResult(ok=False, message=f"Connection failed: {_safe_err(exc)}")

    def list_files(self, pattern: str | None = None) -> list[RemoteFile]:
        patterns: list[str] = (
            [p.strip() for p in pattern.split(",")]
            if pattern
            else self.config.get("filename_patterns", ["*"])
        )
        remote_dir = self.config.get("remote_path", "/")

        try:
            client = self._connect()
            sftp = client.open_sftp()
        except Exception as exc:
            raise RuntimeError(f"SFTP connection failed: {_safe_err(exc)}")

        results: list[RemoteFile] = []
        try:
            for attr in sftp.listdir_attr(remote_dir):
                name = attr.filename or ""
                if not name or name.startswith("."):
                    continue
                if not any(fnmatch.fnmatch(name, p) for p in patterns):
                    continue

                remote_path = f"{remote_dir.rstrip('/')}/{name}"
                ext = Path(name).suffix.lower()
                supported, requires_ocr, _ = extension_info(ext)

                mtime_ts = attr.st_mtime or 0
                mtime = datetime.fromtimestamp(mtime_ts, tz=timezone.utc).isoformat()
                size = attr.st_size or 0
                remote_id = hashlib.sha256(
                    f"{remote_path}:{size}:{mtime}".encode()
                ).hexdigest()[:16]

                results.append(RemoteFile(
                    remote_path=remote_path,
                    filename=name,
                    extension=ext,
                    size_bytes=size,
                    modified_at=mtime,
                    remote_id=remote_id,
                    supported=supported,
                    requires_ocr=requires_ocr,
                ))
        finally:
            sftp.close()
            client.close()

        return results

    def download_file(self, remote_path: str, destination: Path) -> DownloadResult:
        try:
            client = self._connect()
            sftp = client.open_sftp()
        except Exception as exc:
            return DownloadResult(ok=False, error=f"Connection failed: {_safe_err(exc)}")

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            sftp.get(remote_path, str(destination))
            data = destination.read_bytes()
            return DownloadResult(
                ok=True,
                local_path=destination,
                size_bytes=len(data),
                content_sha256=hashlib.sha256(data).hexdigest(),
            )
        except Exception as exc:
            return DownloadResult(ok=False, error=f"Download failed: {_safe_err(exc)}")
        finally:
            sftp.close()
            client.close()
