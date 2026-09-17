"""Brand-mention detection in answer text.

A citation says an engine *linked* to the client; a mention says it *named*
the client. Both matter, and they often disagree (an answer can praise a
vendor without linking, or link a page without naming the brand). Detection is
exact, word-bounded and case-insensitive over the brand name and aliases, and
the sentence containing each match is returned so an analyst can read how the
engine described the client next to its competitors.

This runs after the engine has answered. Nothing here ever touches the prompt.
"""

from __future__ import annotations

import re
from typing import Final

from src.modules.prompt_tracking.schemas import ClientProfile, MentionSnippet

__all__ = ["CLIENT", "detect_mentions", "split_sentences"]

CLIENT: Final = "client"
_SNIPPET_CHARS: Final = 600
# Sentence boundary: terminal punctuation followed by whitespace, or a newline.
_SENTENCE_BREAK: Final = re.compile(r"(?<=[.!?])\s+|\n+")
_MARKUP: Final = re.compile(r"\[\d+\]|\*\*|__|^[\-\*•]\s+", re.M)


def split_sentences(text: str) -> list[str]:
    """Split answer text into trimmed sentences, dropping markdown noise."""
    cleaned = _MARKUP.sub("", text)
    return [s.strip() for s in _SENTENCE_BREAK.split(cleaned) if s and s.strip()]


def detect_mentions(text: str, client: ClientProfile) -> list[MentionSnippet]:
    """Sentences naming the client (entity `client`) or a competitor (entity label).

    Longer terms are tried first so `GEP SMART` is reported as that alias rather
    than as `GEP` twice. One snippet per sentence per entity.
    """
    if not text.strip():
        return []
    terms: list[tuple[str, str]] = [(t, CLIENT) for t in client.brand_terms()]
    terms += sorted(client.competitor_terms().items(), key=lambda kv: -len(kv[0]))
    patterns = [
        (re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.I), term, entity)
        for term, entity in terms
    ]

    found: list[MentionSnippet] = []
    for sentence in split_sentences(text):
        seen_entities: set[str] = set()
        for pattern, term, entity in patterns:
            if entity in seen_entities or not pattern.search(sentence):
                continue
            seen_entities.add(entity)
            found.append(
                MentionSnippet(entity=entity, term=term, snippet=sentence[:_SNIPPET_CHARS])
            )
    return found
