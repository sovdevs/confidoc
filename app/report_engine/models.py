"""Pure data models for the report engine. Zero external dependencies."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AssetItem:
    id: str
    path: str      # relative to report_dir
    caption: str = ""
    tags: list[str] = field(default_factory=list)

    def abs_path(self, report_dir: Path) -> Path:
        return report_dir / self.path


@dataclass
class AssetManifest:
    images:      list[AssetItem] = field(default_factory=list)
    charts:      list[AssetItem] = field(default_factory=list)
    screenshots: list[AssetItem] = field(default_factory=list)
    tables:      list[AssetItem] = field(default_factory=list)

    def all_items(self) -> list[AssetItem]:
        return self.images + self.charts + self.screenshots + self.tables

    def by_path(self, path: str) -> AssetItem | None:
        for item in self.all_items():
            if item.path == path or item.id == path:
                return item
        return None


@dataclass
class Theme:
    logo:         str = ""
    accent_color: str = "#1d4ed8"
    font_family:  str = "Georgia, 'Times New Roman', serif"
    footer_text:  str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Theme":
        return cls(
            logo=d.get("logo", ""),
            accent_color=d.get("accent_color", "#1d4ed8"),
            font_family=d.get("font_family", "Georgia, 'Times New Roman', serif"),
            footer_text=d.get("footer_text", ""),
        )

    @classmethod
    def default(cls) -> "Theme":
        return cls()


@dataclass
class ReportPackage:
    """Everything the renderer needs. No Confidoc types."""
    markdown:   str
    assets:     AssetManifest
    theme:      Theme
    report_dir: Path
    title:      str = "Report"
    subtitle:   str = ""
