"""Sentence-level helpers shared by the connectors for claim attribution.

Engines attribute sources to spans of their answer (OpenAI annotation indices,
Gemini grounding segments, Perplexity `[n]` markers, AI Overview text blocks).
The tracker stores the *sentence* each source supports, so the analyst can read
"this claim is attributed to coupa.com" without the raw payload.
"""

from __future__ import annotations

import re
from typing import Final

__all__ = ["MAX_SENTENCE", "claim_sentence", "sentence_at", "sentences_with_marker"]

MAX_SENTENCE: Final = 600
_BOUNDARY: Final = re.compile(r"[.!?]\s|\n")
_LINK_ONLY: Final = re.compile(r"^[\s(\[]*\[?[^\]]*\]\(https?://[^)]+\)[\s)\].]*$")
_LEADING_LINK: Final = re.compile(r"^\s*\(?\[[^\]]*\]\(https?://[^)]+\)\)?")
_INLINE_LINK: Final = re.compile(r"\s*\(?\[([^\]]*)\]\(https?://[^)]+\)\)?")


def sentence_at(text: str, index: int) -> tuple[str, int, int]:
    """The sentence containing character `index`, with its [start, end) span.

    Boundaries are sentence punctuation followed by whitespace, or a newline.
    Markdown bullets and heading marks at the start are stripped from the
    returned sentence but not from the span.
    """
    if not text:
        return "", 0, 0
    index = max(0, min(index, len(text) - 1))
    start = 0
    for match in _BOUNDARY.finditer(text, 0, index + 1):
        start = match.end()
    end_match = _BOUNDARY.search(text, index)
    end = (
        end_match.start() + 1
        if end_match and end_match.group()[0] != "\n"
        else (end_match.start() if end_match else len(text))
    )
    sentence = text[start:end].strip().lstrip("-*#>0123456789. ").strip()
    return sentence[:MAX_SENTENCE], start, end


def sentences_with_marker(text: str, marker: int) -> list[tuple[str, int, int]]:
    """Every sentence containing the citation marker `[marker]`, in order."""
    pattern = re.compile(rf"\[{marker}\]")
    seen: set[int] = set()
    out: list[tuple[str, int, int]] = []
    for match in pattern.finditer(text):
        sentence, start, end = sentence_at(text, match.start())
        if start in seen or not sentence:
            continue
        seen.add(start)
        out.append((sentence, start, end))
    return out


def claim_sentence(text: str, index: int) -> tuple[str, int, int]:
    """`sentence_at`, but readable for ChatGPT-style answers.

    ChatGPT renders a citation as a bare markdown link *after* the sentence it
    supports; that link resolves to the sentence before it, and inline link
    markup is stripped so the claim reads as prose.
    """
    sentence, start, end = sentence_at(text, index)
    raw = text[start:end]
    leading = _LEADING_LINK.match(raw)
    glued = leading is not None and index - start < leading.end()
    if (_LINK_ONLY.match(sentence) or glued) and start > 0:
        sentence, start, end = sentence_at(text, max(start - 2, 0))
    cleaned = _INLINE_LINK.sub("", sentence).strip()
    return (cleaned or sentence)[:MAX_SENTENCE], start, end
