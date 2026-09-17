"""Maps prompts to client landing pages and flags content gaps.

The mapper works from a URL inventory supplied by the analyst (or a future
sitemap connector). It scores each URL's path slug against the prompt, its
core keyword and its subtopic by token overlap, and declares a content gap when
nothing clears the threshold. Token matching is transparent on purpose: an
analyst can look at a slug and see why it won.
"""

from __future__ import annotations

import re
from typing import Final
from urllib.parse import urlsplit

from src.core.schemas import StrictModel
from src.modules.prompt_tracking.schemas import PromptCandidate

__all__ = ["MappingResult", "UrlMapper", "content_gap_label"]

_STOPWORDS: Final = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "best",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "my",
        "of",
        "on",
        "or",
        "should",
        "that",
        "the",
        "to",
        "what",
        "which",
        "who",
        "why",
        "with",
        "www",
        "html",
        "htm",
        "php",
        "en",
        "us",
        "com",
        "page",
        "pages",
        "index",
        "default",
    }
)
_TOKEN = re.compile(r"[a-z0-9]+")


class MappingResult(StrictModel):
    """Outcome of mapping one prompt."""

    mapped_url: str | None
    score: float
    content_gap: bool
    label: str


def content_gap_label(subtopic: str) -> str:
    """The blueprint's gap marker, e.g. `[CONTENT GAP: Need Procurement Software Page]`."""
    return f"[CONTENT GAP: Need {subtopic.strip().title()} Page]"


class UrlMapper:
    """Token-overlap mapper from prompts to landing pages."""

    def __init__(self, landing_pages: list[str], *, threshold: float = 0.34) -> None:
        """Index the inventory once.

        Args:
            landing_pages: Absolute client URLs.
            threshold: Minimum overlap score to accept a mapping.
        """
        self._threshold = threshold
        self._index: list[tuple[str, frozenset[str]]] = []
        for url in landing_pages:
            tokens = _slug_tokens(url)
            if tokens:
                self._index.append((url, tokens))

    def map(self, candidate: PromptCandidate) -> MappingResult:
        """Find the best landing page for `candidate` or flag a gap."""
        query = _tokens(f"{candidate.core_keyword} {candidate.subtopic} {candidate.prompt_text}")
        best_url: str | None = None
        best_score = 0.0
        for url, slug in self._index:
            overlap = len(query & slug)
            if overlap == 0:
                continue
            # Weight towards slug coverage: a short slug fully covered beats a
            # long slug barely touched, but two shared tokens always matter.
            score = overlap / len(slug) + (0.15 if overlap >= 2 else 0.0)
            if score > best_score:
                best_url, best_score = url, score

        if best_url is not None and best_score >= self._threshold:
            return MappingResult(
                mapped_url=best_url, score=round(best_score, 3), content_gap=False, label=best_url
            )
        return MappingResult(
            mapped_url=None,
            score=round(best_score, 3),
            content_gap=True,
            label=content_gap_label(candidate.subtopic),
        )


def _tokens(text: str) -> frozenset[str]:
    """Lower-case alphanumeric tokens minus stopwords, with a light plural fold."""
    out = set()
    for token in _TOKEN.findall(text.lower()):
        if token in _STOPWORDS or len(token) < 2:
            continue
        out.add(token[:-1] if token.endswith("s") and len(token) > 3 else token)
    return frozenset(out)


def _slug_tokens(url: str) -> frozenset[str]:
    """Tokens from the path (and host label for single-topic subdomains)."""
    parts = urlsplit(url if "://" in url else f"//{url}")
    path = parts.path or ""
    return _tokens(path.replace("/", " "))
