"""Load LLM export prompt files from data/llm_export_prompts/.

Each prompt is a Markdown file with optional YAML front matter:

    ---
    id: summarize_medical_100_words
    title: Summarize medical report in 100 words
    task_type: summary
    language: en
    ---

    Prompt body text...

If no front matter exists, the filename stem is used as the id/title fallback.
"""

from __future__ import annotations
import re
from typing import TypedDict

from app.config import settings


class PromptMeta(TypedDict):
    id: str
    title: str
    task_type: str
    language: str
    prompt_text: str


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text.strip()
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, text[m.end():].strip()


def load_prompts() -> list[PromptMeta]:
    prompts_dir = settings.llm_export_prompts_dir
    if not prompts_dir.exists():
        return []
    results: list[PromptMeta] = []
    for path in sorted(prompts_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)
        stem = re.sub(r"^\d+_", "", path.stem)  # strip "01_" prefix
        results.append(PromptMeta(
            id=meta.get("id", stem),
            title=meta.get("title", stem.replace("_", " ").title()),
            task_type=meta.get("task_type", "custom"),
            language=meta.get("language", "en"),
            prompt_text=body,
        ))
    return results


def get_prompt_by_id(prompt_id: str) -> PromptMeta | None:
    for p in load_prompts():
        if p["id"] == prompt_id:
            return p
    return None
