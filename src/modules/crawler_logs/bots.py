"""The crawler catalogue and user-agent classification.

Tokens are matched case-insensitively at token boundaries, most specific first,
in one compiled alternation. `Google-Extended` and `Applebot-Extended` are
robots.txt tokens, not user agents, and are deliberately absent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from src.integrations.schemas import Engine
from src.modules.crawler_logs.schemas import BotSpecOut

__all__ = ["CATALOGUE", "BotSpec", "catalogue_out", "classify", "spec_for"]

TRAINING: Final = "training"
INDEX: Final = "index"
LIVE_FETCH: Final = "live_fetch"

# Vendors whose published IP ranges are bundled in ranges.json.
VERIFIABLE_VENDORS: Final = frozenset({"openai", "perplexity", "google", "apple"})


@dataclass(frozen=True)
class BotSpec:
    """One catalogued crawler."""

    name: str
    token: str
    vendor: str
    purpose: str
    engine: Engine | None = None

    @property
    def verifiable(self) -> bool:
        """True when the vendor publishes ranges we can check a hit against."""
        return self.vendor in VERIFIABLE_VENDORS


CATALOGUE: Final[tuple[BotSpec, ...]] = (
    # OpenAI — three distinct agents with three distinct meanings.
    BotSpec("OAI-SearchBot", "OAI-SearchBot", "openai", INDEX, Engine.CHATGPT_SEARCH),
    BotSpec("ChatGPT-User", "ChatGPT-User", "openai", LIVE_FETCH, Engine.CHATGPT_SEARCH),
    BotSpec("GPTBot", "GPTBot", "openai", TRAINING),
    # Perplexity
    BotSpec("Perplexity-User", "Perplexity-User", "perplexity", LIVE_FETCH, Engine.PERPLEXITY),
    BotSpec("PerplexityBot", "PerplexityBot", "perplexity", INDEX, Engine.PERPLEXITY),
    # Anthropic (no published ranges)
    BotSpec("Claude-SearchBot", "Claude-SearchBot", "anthropic", INDEX),
    BotSpec("Claude-User", "Claude-User", "anthropic", LIVE_FETCH),
    BotSpec("ClaudeBot", "ClaudeBot", "anthropic", TRAINING),
    BotSpec("Claude-Web", "Claude-Web", "anthropic", TRAINING),
    # Google — Googlebot feeds AI Overviews but crawls everything; never drives a card.
    BotSpec("Google-CloudVertexBot", "Google-CloudVertexBot", "google", INDEX),
    BotSpec("GoogleOther", "GoogleOther", "google", TRAINING),
    BotSpec("Googlebot", "Googlebot", "google", INDEX),
    # Others
    BotSpec("Applebot", "Applebot", "apple", INDEX),
    BotSpec("Bytespider", "Bytespider", "bytedance", TRAINING),
    BotSpec("Amazonbot", "Amazonbot", "amazon", INDEX),
    BotSpec("CCBot", "CCBot", "commoncrawl", TRAINING),
    BotSpec("meta-externalfetcher", "meta-externalfetcher", "meta", LIVE_FETCH),
    BotSpec("meta-externalagent", "meta-externalagent", "meta", TRAINING),
    BotSpec("DuckAssistBot", "DuckAssistBot", "duckduckgo", LIVE_FETCH),
    BotSpec("MistralAI-User", "MistralAI-User", "mistral", LIVE_FETCH),
    BotSpec("AI2Bot", "AI2Bot", "ai2", TRAINING),
    BotSpec("YouBot", "YouBot", "you", INDEX),
    BotSpec("cohere-ai", "cohere-ai", "cohere", TRAINING),
)

_BY_NAME: Final = {spec.name: spec for spec in CATALOGUE}
_PATTERN: Final = re.compile(
    "|".join(
        f"(?P<b{i}>(?<![A-Za-z0-9]){re.escape(spec.token)}(?![A-Za-z0-9]))"
        for i, spec in enumerate(CATALOGUE)
    ),
    re.IGNORECASE,
)


def classify(user_agent: str) -> BotSpec | None:
    """The catalogued crawler named in `user_agent`, or None for everything else."""
    if not user_agent:
        return None
    match = _PATTERN.search(user_agent)
    if match is None:
        return None
    return CATALOGUE[int(match.lastgroup[1:])]  # type: ignore[index]


def spec_for(name: str) -> BotSpec | None:
    """Look a crawler up by its catalogue name."""
    return _BY_NAME.get(name)


def catalogue_out() -> list[BotSpecOut]:
    """The catalogue in API form."""
    return [
        BotSpecOut(
            name=s.name,
            token=s.token,
            vendor=s.vendor,
            purpose=s.purpose,
            engine=s.engine,
            verifiable=s.verifiable,
        )
        for s in CATALOGUE
    ]
