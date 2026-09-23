"""Data contracts every connector returns to the module layer.

Design stance: four engines with four response shapes collapse into one
`EngineAnswer` here, so the prompt-tracking module never sees a vendor payload.
Anything vendor-specific that later analysis might need (a response id, the
consulted-but-not-cited URL list) is carried as plain typed fields, never as a
raw dict.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field

from src.core.schemas import StrictModel

__all__ = [
    "Citation",
    "CitationClaim",
    "Engine",
    "EngineAnswer",
    "KeywordRecord",
    "KeywordSource",
    "OrganicResult",
    "SerpSnapshot",
    "SourceSnippet",
]


class Engine(StrEnum):
    """AI answer engines the tracker audits.

    Values name what was *actually called*, not the consumer product it
    approximates — `CHATGPT_SEARCH` is the OpenAI Responses API with the
    `web_search` tool, which is the closest programmatic proxy for ChatGPT
    Search, not ChatGPT itself.
    """

    GOOGLE_AI_OVERVIEW = "GOOGLE_AI_OVERVIEW"
    CHATGPT_SEARCH = "CHATGPT_SEARCH"
    PERPLEXITY = "PERPLEXITY"
    GEMINI = "GEMINI"

    @property
    def honours_locale(self) -> bool:
        """True when the vendor API accepts a location for this engine.

        Gemini's Developer API `google_search` tool takes an empty object and
        has no location field; a Gemini sample follows the billing account's
        country. The UI says so rather than implying a locale it cannot set.
        """
        return self is not Engine.GEMINI


class Citation(StrictModel):
    """One source an engine attributed its answer to."""

    url: str = Field(min_length=1)
    domain: str = Field(min_length=1, description="Registrable domain, e.g. `gep.com`.")
    title: str | None = None
    position: int = Field(ge=1, description="1-indexed order of first appearance.")
    resolved: bool = Field(
        default=True,
        description="False when `url` is still a vendor redirect whose real "
        "destination could not be resolved (see Gemini connector).",
    )


class CitationClaim(StrictModel):
    """A sentence of the answer and the source the engine attributed it to."""

    url: str = Field(min_length=1)
    sentence: str = Field(min_length=1, max_length=600)
    start: int = Field(default=0, ge=0, description="Character span in the answer text.")
    end: int = Field(default=0, ge=0)


class SourceSnippet(StrictModel):
    """The passage of a source the engine surfaced, with its date when known."""

    url: str = Field(min_length=1)
    snippet: str = Field(default="", max_length=1000)
    title: str | None = None
    date: str | None = Field(default=None, description="Vendor-reported publish/update date.")


class EngineAnswer(StrictModel):
    """A single sampled answer from one engine for one prompt."""

    engine: Engine
    model: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    answer_text: str = ""
    web_triggered: bool = Field(
        description="Engine-specific definition: OpenAI ran a web_search_call; Gemini "
        "returned groundingMetadata; Perplexity returned citations; Google "
        "rendered an AI Overview."
    )
    citations: list[Citation] = Field(default_factory=list)
    consulted_urls: list[str] = Field(
        default_factory=list,
        description="URLs the engine reports having read but not cited inline.",
    )
    search_queries: list[str] = Field(
        default_factory=list,
        description="The sub-queries the engine itself searched to answer (query fan-out).",
    )
    citation_claims: list[CitationClaim] = Field(
        default_factory=list, description="Which sentence each cited source supports."
    )
    source_snippets: list[SourceSnippet] = Field(
        default_factory=list, description="Passages and dates of surfaced sources."
    )
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    latency_ms: float = Field(default=0.0, ge=0.0)
    response_id: str | None = None

    @property
    def cited_domains(self) -> list[str]:
        """Distinct registrable domains in citation order."""
        seen: list[str] = []
        for citation in self.citations:
            if citation.domain not in seen:
                seen.append(citation.domain)
        return seen


class KeywordSource(StrEnum):
    """Which Semrush report a keyword row came from."""

    PHRASE_QUESTIONS = "PHRASE_QUESTIONS"
    PHRASE_ALL = "PHRASE_ALL"
    PHRASE_RELATED = "PHRASE_RELATED"


class KeywordRecord(StrictModel):
    """One keyword row from Semrush, normalised."""

    keyword: str = Field(min_length=1)
    search_volume: int = Field(ge=0)
    cpc: float = Field(default=0.0, ge=0.0)
    competition: float = Field(default=0.0, ge=0.0, le=1.0)
    num_results: int = Field(default=0, ge=0)
    database: str = Field(min_length=2)
    source: KeywordSource


class OrganicResult(StrictModel):
    """One classic (blue-link) Google organic result."""

    position: int = Field(ge=1, description="1-indexed rank as Google rendered it.")
    url: str = Field(min_length=1)
    domain: str = Field(min_length=1, description="Registrable domain.")
    title: str | None = None
    snippet: str | None = Field(default=None, max_length=1000)


class SerpSnapshot(StrictModel):
    """Google SERP features relevant to the tracker, from SerpApi."""

    query: str = Field(min_length=1)
    device: str = Field(default="desktop")
    ai_overview_present: bool
    ai_overview_text: str = ""
    ai_overview_references: list[Citation] = Field(default_factory=list)
    paa_questions: list[str] = Field(default_factory=list)
    related_searches: list[str] = Field(default_factory=list)
    ai_overview_claims: list[CitationClaim] = Field(
        default_factory=list, description="AI Overview sentence per inline source link."
    )
    organic_results: list[OrganicResult] = Field(default_factory=list)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def organic_domains(self) -> list[str]:
        """Registrable domains of the organic results in rank order (may repeat)."""
        return [r.domain for r in self.organic_results]
