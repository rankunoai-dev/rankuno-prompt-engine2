"""Parsing of analyst-supplied prompt lists.

Accepted line format, one prompt per line:

    What is the best procurement software?
    How does GEP compare to Coupa? | procurement software | Vendor Comparison
    # comments and blank lines are ignored

Fields after the first `|` are optional: keyword (used for search volume and the
keyword-rank lookup) and subtopic (used for content-gap labels).
"""

from __future__ import annotations

from pathlib import Path

from src.modules.prompt_tracking.schemas import CustomPrompt

__all__ = ["parse_prompt_lines", "read_prompts_file"]


def parse_prompt_lines(text: str) -> list[CustomPrompt]:
    """Parse `text` into prompts, de-duplicated case-insensitively in order."""
    prompts: list[CustomPrompt] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        prompt_text = parts[0]
        if len(prompt_text) < 3:
            continue
        key = prompt_text.lower()
        if key in seen:
            continue
        seen.add(key)
        prompts.append(
            CustomPrompt(
                prompt_text=prompt_text,
                keyword=parts[1] if len(parts) > 1 and parts[1] else None,
                subtopic=parts[2] if len(parts) > 2 and parts[2] else None,
            )
        )
    return prompts


def read_prompts_file(path: Path) -> list[CustomPrompt]:
    """Read and parse a prompts file (UTF-8)."""
    return parse_prompt_lines(path.read_text(encoding="utf-8"))
