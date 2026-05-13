import re
import yaml
from pathlib import Path


def extract_front_matter(page1_md: str) -> tuple[dict, str]:
    """Parse YAML front matter from the first page's markdown. Returns (metadata, body)."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", page1_md, re.DOTALL)
    if not match:
        return {}, page1_md
    try:
        metadata = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        metadata = {}
    body = page1_md[match.end():]
    return metadata, body


def assemble(pages_md: list[str], source_filename: str) -> str:
    """Merge per-page Markdown fragments into one complete document."""
    if not pages_md:
        return ""

    metadata, first_body = extract_front_matter(pages_md[0])

    # Fallback metadata from filename if fields are missing
    if not metadata.get("titolo"):
        metadata.setdefault("titolo", Path(source_filename).stem)
    for field in ("comune", "tipo", "anno"):
        metadata.setdefault(field, "UNKNOWN")

    # Collect all page bodies; skip any stray front matter blocks on later pages
    bodies = [first_body.strip()]
    for page_md in pages_md[1:]:
        # Strip any front matter Gemini may have incorrectly added on non-first pages
        _, body = extract_front_matter(page_md)
        bodies.append(body.strip())

    full_body = "\n\n".join(b for b in bodies if b)

    # Backfill anno if Gemini couldn't find it on page 1 — scan the full document.
    # Exclude years embedded in law citations like "L.R. 12/2005" or "D.Lgs. n.42/2004"
    # by requiring the year NOT be preceded by a slash.
    if not metadata.get("anno") or str(metadata.get("anno")) == "UNKNOWN":
        years = re.findall(r'(?<!/)\b(20[12]\d)\b(?!/)', full_body)
        if years:
            from collections import Counter
            metadata["anno"] = int(Counter(years).most_common(1)[0][0])

    # Ensure exactly one # heading exists at the top of the body
    if not re.search(r"^# ", full_body, re.MULTILINE):
        full_body = f"# {metadata.get('titolo', source_filename)}\n\n{full_body}"

    yaml_block = yaml.dump(metadata, allow_unicode=True, default_flow_style=False).strip()
    return f"---\n{yaml_block}\n---\n\n{full_body}\n"
