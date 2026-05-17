from app.connectors.base import SourceConnector
from app.connectors.sftp_connector import SFTPConnector
from app.connectors.webdav_connector import WebDAVConnector
from app.connectors.github_connector import GitHubConnector


def get_connector(source_config: dict) -> SourceConnector:
    kind = source_config.get("type", "").lower()
    if kind == "sftp":
        return SFTPConnector(source_config)
    if kind in ("webdav", "nextcloud"):
        return WebDAVConnector(source_config)
    if kind == "github":
        return GitHubConnector(source_config)
    raise ValueError(f"Unknown source type: {kind!r}")
