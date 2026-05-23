"""Asset discovery: scan report_dir/assets/* and return an AssetManifest."""
from __future__ import annotations
from pathlib import Path
from .models import AssetItem, AssetManifest

_IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg'}
_TABLE_EXT = {'.csv', '.tsv'}


def _scan(folder: Path, report_dir: Path) -> list[AssetItem]:
    if not folder.exists():
        return []
    items = []
    for p in sorted(folder.iterdir()):
        if p.is_file() and not p.name.startswith('.'):
            rel = p.relative_to(report_dir).as_posix()
            caption = p.stem.replace('_', ' ').replace('-', ' ').title()
            items.append(AssetItem(id=p.stem, path=rel, caption=caption))
    return items


def discover(report_dir: Path) -> AssetManifest:
    """Return an AssetManifest by scanning the assets/ subdirectory."""
    a = report_dir / "assets"
    return AssetManifest(
        images=      _scan(a / "images",      report_dir),
        charts=      _scan(a / "charts",      report_dir),
        screenshots= _scan(a / "screenshots", report_dir),
        tables=      _scan(a / "tables",      report_dir),
    )
