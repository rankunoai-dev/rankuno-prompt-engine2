"""Data contracts for the prompt-tracking engine.

Design stance: the blueprint's original `CitationSnapshot` recorded a single
boolean per engine per day. AI answers are non-deterministic, so one sample is a
coin flip dressed as a measurement. Snapshots here aggregate *N* samples and
store rates, so month-over-month velocity compares distributions, not flips.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, field_validator

from src.core.domains import normalize_domain
from src.core.locale import Locale
from src.core.schemas import StrictModel
from src.integrations.schemas import (
    Citation,
    CitationClaim,
    Engine,
    KeywordSource,
    OrganicResult,
    SourceSnippet,
)

__all__ = [
    "AnswerSample",
    "ClientProfile",
    "CitationSnapshot",
    "CustomPrompt",
    "DecisionStage",
    "IntentAction",
    "IntentDecision",
    "MasterPromptRecord",
    "MentionSnippet",
    "OrganicRankSnapshot",
    "OrganicVelocityReport",
    "PipelineInput",
    "PipelinePhase",
    "PipelineProgress",
    "PromptCandidate",
    "PromptType",
    "RankQueryKind",
    "SearchIntent",
    "TrackerRunSummary",
    "StabilityReport",
    "StabilityState",
    "VelocityReport",
    "Verdict",
    "prompt_id_for",
]


class SearchIntent(StrEnum):
    """Classic search-intent taxonomy."""

    INFORMATIONAL = "INFORMATIONAL"
    COMMERCIAL = "COMMERCIAL"
    TRANSACTIONAL = "TRANSACTIONAL"
    NAVIGATIONAL = "NAVIGATIONAL"


class DecisionStage(StrEnum):
    """Buyer-journey stage a prompt belongs to."""

    AWARENESS = "AWARENESS"
    CONSIDERATION = "CONSIDERATION"
    DECISION = "DECISION"
    POST_PURCHASE = "POST_PURCHASE"


class PromptType(StrEnum):
    """Whether the prompt names the client's brand."""

    BRANDED = "BRANDED"
    NON_BRANDED = "NON_BRANDED"


class IntentAction(StrEnum):
    """Outcome of the 3-layer intent gate."""

    KEEP = "KEEP"
    REJECT = "REJECT"


class Verdict(StrEnum):
    """Final recommendation for a prompt in the master sheet."""

    KEEP = "KEEP"
    DROP = "DROP"


class ClientProfile(StrictModel):
    """Everything the tracker needs to know about one client LOB."""

    brand_name: str = Field(min_length=1)
    aliases: list[str] = Field(
        default_factory=list, description="Other names the brand appears under (GEP SMART, NEXXE)."
    )
    domains: list[str] = Field(min_length=1, description="Client domains, bare hosts or URLs.")
    competitor_domains: list[str] = Field(default_factory=list)
    competitor_names: list[str] = Field(
        default_factory=list,
        description="Competitor brand names for mention detection (SAP Ariba, Coupa). When "
        "empty, the first label of each competitor domain is used.",
    )
    lob: str = Field(min_length=1, description="Line of business, e.g. 'Procurement Software'.")
    seed_keywords: list[str] = Field(min_length=1)
    subtopics: list[str] = Field(
        default_factory=list,
        description="Optional analyst-defined subtopics. Seed keywords are used when empty.",
    )
    landing_pages: list[str] = Field(
        default_factory=list, description="Client URLs available for mapping."
    )

    @field_validator(
        "domains",
        "competitor_domains",
        "competitor_names",
        "seed_keywords",
        "aliases",
        "landing_pages",
    )
    @classmethod
    def _strip_blanks(cls, values: list[str]) -> list[str]:
        """Trim whitespace and drop empties so downstream matching is exact."""
        return [v.strip() for v in values if v and v.strip()]

    def brand_terms(self) -> list[str]:
        """Brand name plus aliases, longest first (greedy matching)."""
        terms = {self.brand_name, *self.aliases}
        return sorted((t for t in terms if t), key=len, reverse=True)

    def competitor_terms(self) -> dict[str, str]:
        """Mention terms per competitor, keyed by term, valued by the competitor label.

        Explicit `competitor_names` are used when given; otherwise the first
        label of each competitor domain (`coupa.com` -> `coupa`) when it is at
        least three characters, so short labels cannot match ordinary words.
        """
        terms: dict[str, str] = {}
        for name in self.competitor_names:
            terms[name] = name
        if not self.competitor_names:
            for domain in self.competitor_domains:
                label = normalize_domain(domain).split(".")[0]
                if len(label) >= 3:
                    terms[label] = normalize_domain(domain)
        return terms


class CustomPrompt(StrictModel):
    """An analyst-supplied prompt, optionally pinned to a keyword and subtopic."""

    prompt_text: str = Field(min_length=3)
    keyword: str | None = Field(
        default=None, description="Seed keyword for volume and keyword-rank lookup."
    )
    subtopic: str | None = None

    @field_validator("prompt_text", "keyword", "subtopic")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        """Trim whitespace; empty optional fields become None."""
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None


class IntentDecision(StrictModel):
    """Result of scoring one prompt through the intent gate."""

    prompt_text: str = Field(min_length=1)
    action: IntentAction
    score: float = Field(ge=0.0, le=1.0)
    entity_type: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    matched_pattern: str | None = None


class PromptCandidate(StrictModel):
    """A prompt that survived generation and is eligible for selection."""

    prompt_text: str = Field(min_length=1)
    core_keyword: str = Field(min_length=1)
    search_volume: int = Field(ge=0)
    subtopic: str = Field(min_length=1)
    prompt_type: PromptType
    search_intent: SearchIntent
    decision_stage: DecisionStage
    keyword_source: KeywordSource | None = Field(
        default=None, description="None when the prompt came from a template."
    )
    intent: IntentDecision


class MentionSnippet(StrictModel):
    """One sentence of an answer that names the client or a competitor."""

    entity: str = Field(
        min_length=1, description="'client' or the competitor label the term belongs to."
    )
    term: str = Field(min_length=1, description="The brand term that matched.")
    snippet: str = Field(min_length=1, max_length=600)


class MentionJudgement(StrictModel):
    """How an engine's sentence framed one entity, as scored by the judge (ADR 0021).

    One row per (sample, entity, sentence). `status` says whether the judge
    answered; `polarity` is meaningful only when it did. The model and rubric
    version travel with every row so a later change never rewrites history.
    """

    prompt_id: str = Field(min_length=8, max_length=16)
    run_id: str = Field(min_length=1)
    engine: Engine
    captured_at: datetime
    entity: str = Field(min_length=1, description="'client' or the competitor label.")
    term: str = Field(min_length=1)
    sentence_sha1: str = Field(min_length=40, max_length=40)
    sentence: str = Field(min_length=1, max_length=600)
    status: str = Field(default="ok", pattern="^(ok|unscored|refused)$")
    polarity: str = Field(
        default="neutral", pattern="^(positive|neutral|negative|not_about_brand)$"
    )
    attributes: list[str] = Field(default_factory=list, max_length=3)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    model: str = Field(min_length=1)
    rubric_version: str = Field(min_length=1)
    judged_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CitationSnapshot(StrictModel):
    """Aggregated citation outcome for one prompt on one engine at one time."""

    engine: Engine
    model: str = Field(min_length=1)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    samples: int = Field(ge=1)
    failed_samples: int = Field(default=0, ge=0)
    web_trigger_rate: float = Field(ge=0.0, le=1.0)
    client_cited_samples: int = Field(ge=0)
    client_citation_rate: float = Field(ge=0.0, le=1.0)
    client_cited: bool = Field(description="Cited in at least half of the successful samples.")
    client_best_rank: int | None = Field(default=None, ge=1)
    client_mean_rank: float | None = Field(default=None, ge=1.0)
    cited_domains: list[str] = Field(
        default_factory=list, description="Union across samples, most frequent first."
    )
    competitor_citations: dict[str, int] = Field(
        default_factory=dict, description="Best (lowest) rank per competitor domain."
    )
    answer_excerpt: str = ""
    response_ids: list[str] = Field(
        default_factory=list, description="Vendor response ids of the samples, for audits."
    )
    reused: bool = Field(
        default=False, description="True when copied from a recent run instead of re-sampled."
    )
    citation_links: list[Citation] = Field(
        default_factory=list,
        description="Every URL cited across samples, best position first, de-duplicated.",
    )
    client_urls: list[str] = Field(
        default_factory=list, description="The client's own URLs among the citations."
    )
    consulted_urls: list[str] = Field(
        default_factory=list, description="URLs the engine read but did not cite inline."
    )
    mention_detected: bool = Field(
        default=False, description="Client named in the answer text of at least one sample."
    )
    mention_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    mention_snippets: list[MentionSnippet] = Field(
        default_factory=list, description="Distinct client-mention sentences, at most ten."
    )
    competitor_mentions: dict[str, int] = Field(
        default_factory=dict, description="Samples in which each competitor was named."
    )


class AnswerSample(StrictModel):
    """One raw engine answer, kept so model shifts can be told apart from content changes."""

    prompt_id: str = Field(min_length=8, max_length=16)
    run_id: str = Field(
        default="",
        description="Pipeline run that captured this sample; empty on samples read "
        "before the column was mapped.",
    )
    engine: Engine
    model: str = Field(min_length=1)
    captured_at: datetime
    response_id: str | None = None
    web_triggered: bool
    client_cited: bool
    client_rank: int | None = Field(default=None, ge=1)
    cited_domains: list[str] = Field(default_factory=list)
    citation_links: list[Citation] = Field(default_factory=list)
    consulted_urls: list[str] = Field(default_factory=list)
    mention_detected: bool = False
    mentions: list[MentionSnippet] = Field(default_factory=list)
    answer_excerpt: str = ""
    answer_text: str = Field(default="", description="Full answer text (cycle 0011).")
    search_queries: list[str] = Field(
        default_factory=list, description="The engine's own sub-queries (query fan-out)."
    )
    citation_claims: list[CitationClaim] = Field(default_factory=list)
    source_snippets: list[SourceSnippet] = Field(default_factory=list)


class RankQueryKind(StrEnum):
    """Which query string an organic ranking was measured for."""

    PROMPT = "PROMPT"
    """The conversational prompt text itself (free: same call as the AI Overview)."""

    KEYWORD = "KEYWORD"
    """The short seed keyword behind the prompt (one extra SerpApi call per keyword)."""


class OrganicRankSnapshot(StrictModel):
    """Client and competitor positions in Google's classic organic results."""

    query: str = Field(min_length=1)
    query_kind: RankQueryKind
    device: str = Field(default="desktop")
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    samples: int = Field(ge=1)
    client_position: int | None = Field(default=None, ge=1, description="Best across samples.")
    client_url: str | None = None
    top_domains: list[str] = Field(
        default_factory=list, description="Top-10 registrable domains, rank order."
    )
    competitor_positions: dict[str, int] = Field(
        default_factory=dict, description="Best position per competitor domain."
    )
    organic_results: list[OrganicResult] = Field(
        default_factory=list, description="Top-10 results with title and snippet (cycle 0011)."
    )
    paa_questions: list[str] = Field(default_factory=list)
    related_searches: list[str] = Field(default_factory=list)


class OrganicVelocityReport(StrictModel):
    """Organic position change between two consecutive windows."""

    prompt_id: str
    query_kind: RankQueryKind
    window_days: int = Field(ge=1)
    current_best_position: int | None = None
    previous_best_position: int | None = None
    position_delta: int | None = Field(
        default=None, description="previous - current; positive means the client moved up."
    )


class MasterPromptRecord(StrictModel):
    """One row of the 20-prompt master research sheet."""

    prompt_id: str = Field(min_length=8, max_length=16)
    lob: str = Field(min_length=1)
    subtopic: str = Field(min_length=1)
    core_keyword: str = Field(min_length=1)
    search_volume: int = Field(ge=0)
    prompt_text: str = Field(min_length=1)
    search_intent: SearchIntent
    decision_stage: DecisionStage
    prompt_type: PromptType
    web_triggers: bool = Field(description="True if any engine triggered a web search.")
    citation_history: list[CitationSnapshot] = Field(default_factory=list)
    organic_history: list[OrganicRankSnapshot] = Field(default_factory=list)
    mapped_url: str | None = None
    content_gap: bool = False
    verdict: Verdict
    verdict_reason: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def latest(self, engine: Engine) -> CitationSnapshot | None:
        """Most recent snapshot for `engine`, if any."""
        matching = [s for s in self.citation_history if s.engine is engine]
        return max(matching, key=lambda s: s.captured_at) if matching else None

    def latest_rank(self, kind: RankQueryKind) -> OrganicRankSnapshot | None:
        """Most recent organic ranking of the given kind, if any."""
        matching = [s for s in self.organic_history if s.query_kind is kind]
        return max(matching, key=lambda s: s.captured_at) if matching else None


class VelocityReport(StrictModel):
    """Change in citation performance between two consecutive windows."""

    prompt_id: str
    engine: Engine
    window_days: int = Field(ge=1)
    current_rate: float = Field(ge=0.0, le=1.0)
    previous_rate: float = Field(ge=0.0, le=1.0)
    rate_delta: float
    current_best_rank: int | None = None
    previous_best_rank: int | None = None
    rank_delta: int | None = Field(
        default=None, description="previous - current; positive means the client moved up."
    )


class StabilityState(StrEnum):
    """How settled a prompt × platform pair is across its recent crawls (ADR 0025)."""

    UNKNOWN = "unknown"
    STABLE = "stable"
    VOLATILE = "volatile"
    FAILING = "failing"


class StabilityReport(StrictModel):
    """The pooled evidence behind a pair's stability verdict, in analyst terms.

    Per-crawl rates are pushed to 0 or 1 by early stop, so the verdict pools the
    window's samples and reads the Wilson band (ADR 0020): stable when the band
    excludes 50% and is narrow, volatile when it straddles 50% or the stored
    verdict flipped repeatedly, failing when no sample succeeded.
    """

    state: StabilityState
    score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="1 - coin_flip(pooled rate); 1 is fully settled. None when not measured.",
    )
    crawls: int = Field(ge=0, description="Crawls admitted to the window.")
    ok_samples: int = Field(default=0, ge=0)
    cited_samples: int = Field(default=0, ge=0)
    rate: float | None = Field(default=None, ge=0.0, le=1.0)
    rate_low: float | None = Field(default=None, ge=0.0, le=1.0)
    rate_high: float | None = Field(default=None, ge=0.0, le=1.0)
    flips: int = Field(default=0, ge=0, description="Verdict changes between consecutive crawls.")
    streak: int = Field(
        default=0, ge=0, description="Newest consecutive crawls sharing the newest verdict."
    )
    newest_at: datetime | None = None
    reason: str


class PipelineInput(StrictModel):
    """Arguments for one tracker run."""

    client: ClientProfile
    engines: list[Engine] = Field(default_factory=lambda: list(Engine))
    samples_per_engine: int | None = Field(default=None, ge=1, le=10)
    resolve_redirects: bool = Field(
        default=False, description="Follow Gemini redirect links to attribute domains."
    )
    write_report: bool = True
    skip_engine_audit: bool = Field(
        default=False,
        description="Harvest, filter and select only. Semrush is still called; no SerpApi.",
    )
    track_keyword_rank: bool = Field(
        default=True,
        description="Also measure Google organic rank for each distinct seed keyword "
        "(one extra SerpApi call per keyword per run). Prompt-text rank is always "
        "captured from the AI Overview call at no extra cost.",
    )
    locale: Locale | None = Field(
        default=None,
        description="Market this run is executed from. None uses the settings default. "
        "Gemini ignores it (no location field in its API).",
    )
    questions_per_keyword: int = Field(default=25, ge=1, le=200)
    custom_prompts: list[CustomPrompt] = Field(
        default_factory=list,
        description="Analyst-supplied prompts. Always tracked; the intent gate is advisory.",
    )
    generate_prompts: bool = Field(
        default=True,
        description="Harvest Semrush and generate the 10+10 set. False = custom prompts only, "
        "no Semrush spend.",
    )
    max_engine_calls: int | None = Field(
        default=None, ge=1, description="Per-run cap on engine calls; overrides settings."
    )
    reuse_within_hours: int | None = Field(
        default=None, ge=0, description="Reuse recent snapshots; overrides settings."
    )
    adaptive_sampling: bool | None = Field(default=None, description="Overrides settings.")
    max_workers: int | None = Field(default=None, ge=1, le=16, description="Overrides settings.")


class PipelinePhase(StrEnum):
    """Where a running pipeline is; emitted with every progress event."""

    HARVEST = "harvest"
    AUDIT = "audit"
    KEYWORD_RANKS = "keyword_ranks"
    REPORT = "report"
    DONE = "done"


class PipelineProgress(StrictModel):
    """One progress event from a running pipeline.

    `done`/`total` count prompt x platform checks (reused snapshots count as done
    at once); `calls` is the number of paid engine calls made so far.
    """

    phase: PipelinePhase
    done: int = Field(ge=0)
    total: int = Field(ge=0)
    calls: int = Field(default=0, ge=0)
    message: str = ""


class TrackerRunSummary(StrictModel):
    """Outcome of one pipeline execution."""

    run_id: str = Field(min_length=8)
    lob: str
    brand_name: str
    started_at: datetime
    finished_at: datetime
    candidates_generated: int = Field(ge=0)
    candidates_kept: int = Field(ge=0)
    prompts_selected: int = Field(ge=0)
    engine_calls: int = Field(ge=0)
    failed_engine_calls: int = Field(ge=0)
    keyword_rank_calls: int = Field(default=0, ge=0)
    reused_snapshots: int = Field(default=0, ge=0)
    calls_refused_by_circuit: int = Field(default=0, ge=0)
    custom_prompts: int = Field(default=0, ge=0)
    model_shifts: list[str] = Field(
        default_factory=list,
        description="Engines whose model string changed since the previous run.",
    )
    estimated_cost_usd: float = Field(ge=0.0)
    semrush_units: int = Field(ge=0)
    report_path: str | None = None
    dataset_path: str | None = Field(
        default=None, description="UI-ready JSON dataset (one row per prompt × engine)."
    )
    warnings: list[str] = Field(default_factory=list)
    records: list[MasterPromptRecord] = Field(default_factory=list)


def prompt_id_for(lob: str, prompt_text: str) -> str:
    """Stable 16-hex id so re-runs append history instead of creating new rows."""
    digest = hashlib.sha256(f"{lob.strip().lower()}|{prompt_text.strip().lower()}".encode())
    return digest.hexdigest()[:16]
