import re
import logging

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = ("titolo", "comune", "tipo", "anno")


def validate_front_matter(metadata: dict) -> list[str]:
    return [f"Missing required front matter field: '{f}'" for f in REQUIRED_FIELDS if not metadata.get(f)]


def validate_heading_hierarchy(markdown_text: str) -> list[str]:
    warnings = []
    prev_level = 0
    for i, line in enumerate(markdown_text.splitlines(), start=1):
        m = re.match(r"^(#{1,4})\s", line)
        if not m:
            continue
        level = len(m.group(1))
        if prev_level and level > prev_level + 1:
            warnings.append(f"Line {i}: heading level skips from {prev_level} to {level} — '{line.strip()}'")
        prev_level = level
    return warnings


def fix_heading_skips(markdown_text: str) -> str:
    lines = markdown_text.splitlines()
    result = []
    prev_level = 0
    for line in lines:
        m = re.match(r"^(#{1,4})\s", line)
        if m:
            level = len(m.group(1))
            if prev_level and level == prev_level + 2:
                # Insert a placeholder at the missing intermediate level
                filler_level = "#" * (prev_level + 1)
                result.append(f"{filler_level} (continued)")
                logger.warning(f"Inserted placeholder heading at level {prev_level + 1} before: {line.strip()}")
            elif prev_level and level > prev_level + 2:
                logger.warning(f"Large heading skip ({prev_level}→{level}), leaving for manual review: {line.strip()}")
            prev_level = level
        result.append(line)
    return "\n".join(result)


def validate_document(markdown_text: str, metadata: dict) -> tuple[str, list[str]]:
    warnings = validate_front_matter(metadata)
    warnings += validate_heading_hierarchy(markdown_text)
    fixed = fix_heading_skips(markdown_text)
    return fixed, warnings
