"""Contracts for projects, tracked prompts, due work and run outcomes."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, SecretStr, field_validator

from src.core.locale import Locale
from src.core.schemas import StrictModel
from src.integrations.schemas import Engine
from src.modules.prompt_tracking.scheduler import parse_interval
from src.modules.prompt_tracking.schemas import (
    CitationSnapshot,
    ClientProfile,
    OrganicRankSnapshot,
    OrganicVelocityReport,
    VelocityReport,
)

__all__ = [
    "ActionCard",
    "ActionEvidence",
    "ActionState",
    "ActionUpdate",
    "AttributeCount",
    "ClaimEntry",
    "ConsolidateRequest",
    "ConsolidatedPosition",
    "Consolidation",
    "DomainShare",
    "DueItem",
    "EngineHealth",
    "EngineStatus",
    "EvidenceQuote",
    "FanoutQuery",
    "FreshnessProfile",
    "INTERVAL_PRESETS",
    "InsightBasis",
    "InsightChange",
    "InsightsView",
    "JobState",
    "MentionContext",
    "PageInventory",
    "PlacementProfile",
    "PositionsView",
    "Project",
    "ProjectAccess",
    "ProjectBase",
    "ProjectCreate",
    "ProjectCredentials",
    "ProjectRunRecord",
    "ProjectUpdate",
    "PromptCapture",
    "PromptDetail",
    "PromptEngineDetail",
    "PromptPosition",
    "PromptResult",
    "RejectedPage",
    "RunJob",
    "RunOutcome",
    "RunProgress",
    "RunRequest",
    "SentimentCoverage",
    "SentimentProfile",
    "TrackedPrompt",
    "TrackedPromptBase",
    "TrackedPromptCreate",
    "TrackedPromptUpdate",
    "TrustShare",
    "WorkBatch",
]

INTERVAL_PRESETS: tuple[tuple[str, str], ...] = (
    ("daily", "Every day"),
    ("2d", "Every 2 days (one-day leap)"),
    ("3d", "Every 3 days (two-day leap)"),
    ("weekly", "Every week"),
    ("2w", "Every 2 weeks"),
    ("monthly", "Every month (30 days)"),
)


def _valid_interval(value: str) -> str:
    parse_interval(value)
    return value.strip().lower()


class ProjectBase(StrictModel):
    """Editable project configuration."""

    name: str = Field(min_length=1, max_length=80)
    client: ClientProfile
    engines: list[Engine] = Field(default_factory=lambda: list(Engine), min_length=1)
    engine_models: dict[Engine, str] = Field(
        default_factory=dict,
        description="Model selection per platform (e.g. CHATGPT_SEARCH -> gpt-4o).",
    )
    interval: str = Field(default="daily", description="Default run interval for prompts.")
    enabled: bool = True
    samples_per_engine: int | None = Field(default=None, ge=1, le=10)
    generate_prompts: bool = Field(
        default=False, description="Also run the Semrush-generated 10+10 set at the interval."
    )
    track_keyword_rank: bool = True
    resolve_redirects: bool = False
    max_engine_calls: int | None = Field(default=None, ge=1)
    reuse_within_hours: int | None = Field(default=None, ge=0)
    consolidation_runs: int = Field(
        default=3,
        ge=1,
        le=50,
        description="Consolidate positions after every N full crawls of the project. Independent "
        "of the run interval: with a 2-day interval and 3, positions are consolidated "
        "every 6 days from all three crawls.",
    )
    sampling_policy: str = Field(
        default="fixed",
        pattern="^(fixed|save|reallocate)$",
        description="fixed: every pair sampled at its interval. save: stable pairs are "
        "sampled less often. reallocate: save, plus extra samples for volatile pairs paid "
        "for by what stretching saved (ADR 0025).",
    )
    locale: Locale | None = Field(
        default=None,
        description="Market every crawl of this project is executed from. None uses the "
        "server default (SERP_GL / SERP_HL / SERP_LOCATION). Frozen once the project has "
        "crawled: changing it mid-history would mix two markets into one trend (ADR 0023).",
    )
    notes: str = Field(default="", max_length=2000)
    sentiment: bool = Field(
        default=True,
        description="Score brand mentions after each crawl (needs ANTHROPIC_API_KEY; ADR 0021).",
    )

    @field_validator("interval")
    @classmethod
    def _interval(cls, value: str) -> str:
        """Reject unknown intervals at save time."""
        return _valid_interval(value)


class ProjectCredentials(StrictModel):
    """The owner credential that unlocks writes to one project (ADR 0019).

    The owner name is public (it is shown beside the lock); the password is not
    stored, only its salted digest. A colon is refused in the owner because the
    pair travels as `owner:password` in the `X-Project-Authorization` header.
    """

    owner: str = Field(min_length=1, max_length=64, pattern=r"^[^:\s][^:]*$")
    password: SecretStr = Field(min_length=8, max_length=128)


class ProjectCreate(ProjectBase):
    """Body for creating a project."""

    credentials: ProjectCredentials | None = Field(
        default=None,
        description="Owner credential. With it set, only its holder may change, run or "
        "delete the project; everyone else reads. Without it the project stays open.",
    )


class ProjectUpdate(StrictModel):
    """Partial update; only supplied fields change."""

    name: str | None = Field(default=None, min_length=1, max_length=80)
    client: ClientProfile | None = None
    engines: list[Engine] | None = Field(default=None, min_length=1)
    engine_models: dict[Engine, str] | None = None
    interval: str | None = None
    enabled: bool | None = None
    samples_per_engine: int | None = Field(default=None, ge=1, le=10)
    generate_prompts: bool | None = None
    track_keyword_rank: bool | None = None
    resolve_redirects: bool | None = None
    max_engine_calls: int | None = Field(default=None, ge=1)
    reuse_within_hours: int | None = Field(default=None, ge=0)
    consolidation_runs: int | None = Field(default=None, ge=1, le=50)
    sampling_policy: str | None = Field(default=None, pattern="^(fixed|save|reallocate)$")
    locale: Locale | None = None
    notes: str | None = Field(default=None, max_length=2000)
    sentiment: bool | None = None

    @field_validator("interval")
    @classmethod
    def _interval(cls, value: str | None) -> str | None:
        """Reject unknown intervals at save time."""
        return _valid_interval(value) if value is not None else None


class Project(ProjectBase):
    """A stored project."""

    id: str = Field(min_length=8)
    created_at: datetime
    updated_at: datetime
    protected: bool = Field(
        default=False, description="Writes need the owner credential (read-only otherwise)."
    )
    owner: str | None = Field(default=None, description="Public name of the credential holder.")


class ProjectAccess(StrictModel):
    """What the caller may do with one project, given the credential it presented."""

    protected: bool
    owner: str | None = None
    can_write: bool


class TrackedPromptBase(StrictModel):
    """Editable prompt configuration. Text is sent to engines verbatim."""

    prompt_text: str = Field(min_length=3, max_length=1000)
    keyword: str | None = None
    subtopic: str | None = None
    important: bool = Field(default=False, description="Starred; surfaces first in the UI.")
    enabled: bool = True
    interval: str | None = Field(default=None, description="Overrides the project interval.")
    engines: list[Engine] | None = Field(
        default=None, min_length=1, description="Overrides the project platforms."
    )
    samples_per_engine: int | None = Field(default=None, ge=1, le=10)

    @field_validator("prompt_text", "keyword", "subtopic")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        """Collapse whitespace only; never change casing or punctuation."""
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None

    @field_validator("interval")
    @classmethod
    def _interval(cls, value: str | None) -> str | None:
        """Reject unknown intervals at save time."""
        return _valid_interval(value) if value is not None else None


class TrackedPromptCreate(TrackedPromptBase):
    """Body for adding a prompt."""


class TrackedPromptUpdate(StrictModel):
    """Partial update; only supplied fields change. Overrides can be cleared as a whole."""

    prompt_text: str | None = Field(default=None, min_length=3, max_length=1000)
    keyword: str | None = None
    subtopic: str | None = None
    important: bool | None = None
    enabled: bool | None = None
    interval: str | None = None
    engines: list[Engine] | None = Field(default=None, min_length=1)
    samples_per_engine: int | None = Field(default=None, ge=1, le=10)
    clear_overrides: bool = Field(
        default=False, description="Reset interval/engines/samples overrides to the project's."
    )

    @field_validator("interval")
    @classmethod
    def _interval(cls, value: str | None) -> str | None:
        """Reject unknown intervals at save time."""
        return _valid_interval(value) if value is not None else None


class TrackedPrompt(TrackedPromptBase):
    """A stored prompt."""

    id: str = Field(min_length=8)
    project_id: str = Field(min_length=8)
    prompt_id: str = Field(
        min_length=8, max_length=16, description="Stable hash shared with the time-series store."
    )
    created_at: datetime
    updated_at: datetime


class DueItem(StrictModel):
    """One prompt that needs sampling on some platforms."""

    prompt: TrackedPrompt
    engines: list[Engine] = Field(min_length=1)
    reason: str


class WorkBatch(StrictModel):
    """Prompts that share a platform set and sample count, run as one pipeline call."""

    engines: list[Engine] = Field(min_length=1)
    samples_per_engine: int | None = None
    prompts: list[TrackedPrompt] = Field(min_length=1)


class RunRequest(StrictModel):
    """Body for `POST /api/projects/{id}/run`."""

    force: bool = Field(default=False, description="Run even if nothing is due.")
    prompt_ids: list[str] = Field(default_factory=list, description="Restrict to these prompts.")
    engines: list[Engine] | None = Field(default=None, description="Restrict to these platforms.")


class RunOutcome(StrictModel):
    """What a project run did."""

    project_id: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    batches: int = Field(ge=0)
    prompts_run: int = Field(ge=0)
    run_ids: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    reason: str = ""
    project_run_id: str | None = Field(default=None, description="Crawl record id, if recorded.")
    consolidation_id: str | None = Field(
        default=None, description="Set when this crawl completed a consolidation window."
    )


class RunProgress(StrictModel):
    """Live progress of one project run, folded across its batches.

    `checks` are prompt x platform audits; `percent` is checks_done / checks_total.
    """

    phase: str = Field(default="queued", description="queued|planning|harvest|audit|…|done")
    message: str = ""
    checks_done: int = Field(default=0, ge=0)
    checks_total: int = Field(default=0, ge=0)
    batches_done: int = Field(default=0, ge=0)
    batches_total: int = Field(default=0, ge=0)
    engine_calls: int = Field(default=0, ge=0)
    percent: float = Field(default=0.0, ge=0.0, le=100.0)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class JobState(StrEnum):
    """Lifecycle of a queued run."""

    QUEUED = "queued"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"

    @property
    def active(self) -> bool:
        """True while the job still has work ahead of it."""
        return self in (JobState.QUEUED, JobState.RUNNING)


class RunJob(StrictModel):
    """A run submitted from the UI or the poller, executed in the background."""

    id: str = Field(min_length=8)
    project_id: str = Field(min_length=8)
    project_name: str
    request: RunRequest
    state: JobState = JobState.QUEUED
    position: int = Field(default=0, ge=0, description="Jobs ahead in the queue (0 = next).")
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    progress: RunProgress = Field(default_factory=RunProgress)
    outcome: RunOutcome | None = None
    error: str | None = None


class ProjectRunRecord(StrictModel):
    """One crawl of a project: a job that produced at least one pipeline run."""

    id: str = Field(min_length=8)
    project_id: str = Field(min_length=8)
    started_at: datetime
    finished_at: datetime
    run_ids: list[str] = Field(default_factory=list)
    prompts_run: int = Field(ge=0)
    batches: int = Field(ge=0)
    statuses: list[str] = Field(default_factory=list)
    full: bool = Field(description="True unless the run was restricted to selected prompts.")


class ConsolidateRequest(StrictModel):
    """Body for `POST /api/projects/{id}/consolidate`."""

    window_runs: int | None = Field(
        default=None, ge=1, le=50, description="Override the project's consolidation window."
    )
    note: str = Field(default="", max_length=500)


class ConsolidatedPosition(StrictModel):
    """One prompt on one platform, positioned over a window of crawls."""

    prompt_id: str
    engine: Engine
    runs: int = Field(ge=1, description="Pipeline runs that contributed.")
    first_run_at: datetime
    last_run_at: datetime
    samples: int = Field(ge=0)
    failed_samples: int = Field(ge=0)
    cited_samples: int = Field(ge=0)
    citation_rate: float = Field(ge=0.0, le=1.0)
    # 95% Wilson bounds on the two rates; None on rows consolidated before they existed.
    citation_rate_low: float | None = Field(default=None, ge=0.0, le=1.0)
    citation_rate_high: float | None = Field(default=None, ge=0.0, le=1.0)
    cited: bool
    mention_samples: int = Field(ge=0)
    mention_rate: float = Field(ge=0.0, le=1.0)
    mention_rate_low: float | None = Field(default=None, ge=0.0, le=1.0)
    mention_rate_high: float | None = Field(default=None, ge=0.0, le=1.0)
    mentioned: bool
    best_rank: int | None = None
    mean_rank: float | None = None
    rank_distribution: dict[str, int] = Field(default_factory=dict)
    cited_domain_share: dict[str, float] = Field(default_factory=dict)
    competitor_citations: dict[str, int] = Field(default_factory=dict)
    organic_prompt_best: int | None = None
    organic_prompt_mean: float | None = None
    organic_keyword_best: int | None = None
    organic_keyword_mean: float | None = None


class Consolidation(StrictModel):
    """A dated position set for a project."""

    id: str = Field(min_length=8)
    project_id: str = Field(min_length=8)
    consolidated_at: datetime
    window_runs: int = Field(ge=1)
    project_run_ids: list[str] = Field(default_factory=list)
    run_ids: list[str] = Field(default_factory=list)
    first_run_at: datetime | None = None
    last_run_at: datetime | None = None
    prompts: int = Field(ge=0)
    trigger: str = Field(description="auto | manual")
    note: str = ""
    positions_count: int = Field(default=0, ge=0)


class PositionsView(StrictModel):
    """What the Results tab shows in consolidated mode."""

    consolidation: Consolidation | None = None
    positions: list[ConsolidatedPosition] = Field(default_factory=list)
    history: list[Consolidation] = Field(default_factory=list)
    runs_since_last: int = Field(default=0, ge=0)


class ActionState(StrictModel):
    """What the analyst recorded on an action card."""

    action_id: str
    status: str = Field(default="open", pattern="^(open|done)$")
    owner: str | None = None
    note: str | None = None
    baseline: dict[str, float | str | None] = Field(default_factory=dict)
    updated_at: datetime


class ActionUpdate(StrictModel):
    """Body for `PUT /api/projects/{id}/actions/{action_id}`."""

    status: str | None = Field(default=None, pattern="^(open|done)$")
    owner: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=1000)


class InsightBasis(StrictModel):
    """What the insights were computed from."""

    consolidation_id: str | None
    computed_from: str = Field(description="consolidation | latest_crawl | none")
    crawls: int = Field(ge=0)
    samples: int = Field(ge=0)
    low_confidence: bool


class EngineHealth(StrictModel):
    """One platform's verdict for the project."""

    engine: Engine
    verdict: str = Field(description="winning | present | invisible | losing")
    losing_to: str | None = None
    cited_rate: float = Field(ge=0.0, le=1.0)
    mention_rate: float = Field(ge=0.0, le=1.0)
    # 95% Wilson bounds on the pooled samples behind the two rates.
    cited_rate_low: float | None = Field(default=None, ge=0.0, le=1.0)
    cited_rate_high: float | None = Field(default=None, ge=0.0, le=1.0)
    mention_rate_low: float | None = Field(default=None, ge=0.0, le=1.0)
    mention_rate_high: float | None = Field(default=None, ge=0.0, le=1.0)
    best_rank: int | None = None
    delta_cited_rate: float | None = None
    samples: int = Field(ge=0)
    crawls: int = Field(ge=0)
    volatility: float = Field(ge=0.0, le=1.0, description="0 stable, 1 coin-flip across samples.")
    prompts: int = Field(ge=0)


class InsightChange(StrictModel):
    """One movement between the last two position sets."""

    kind: str
    prompt_id: str
    prompt_text: str
    engine: Engine
    before: str
    after: str
    text: str


class EvidenceQuote(StrictModel):
    """A sentence from an engine answer, with provenance."""

    text: str
    engine: Engine
    run_id: str | None = None
    captured_at: datetime | None = None
    entity: str | None = None
    url: str | None = Field(default=None, description="Source the engine attached to the sentence.")


class AttributeCount(StrictModel):
    """An attribute the engines attach to an entity, with how often and one example."""

    attribute: str
    count: int = Field(ge=1)
    example: str


class SentimentProfile(StrictModel):
    """How one platform frames one entity over the window (ADR 0021)."""

    engine: Engine
    entity: str = Field(description="'client' or the competitor label.")
    judged: int = Field(ge=0, description="Sentences with an ok verdict under the current rubric.")
    positive: int = Field(ge=0)
    neutral: int = Field(ge=0)
    negative: int = Field(ge=0)
    not_about_brand: int = Field(ge=0)
    unscored: int = Field(ge=0, description="Unscored, refused, or scored under an older rubric.")
    negative_share: float = Field(ge=0.0, le=1.0)
    negative_share_low: float | None = Field(default=None, ge=0.0, le=1.0)
    negative_share_high: float | None = Field(default=None, ge=0.0, le=1.0)
    attributes: list[AttributeCount] = Field(default_factory=list)
    worst: list[EvidenceQuote] = Field(default_factory=list, description="Negative outliers.")
    model: str | None = None
    rubric_version: str | None = None


class SentimentCoverage(StrictModel):
    """Whether the sentiment figures can be trusted, and why not when they cannot."""

    configured: bool = Field(description="A judge produced rows for this window.")
    judged: int = Field(ge=0)
    unscored: int = Field(ge=0)
    model: str | None = None
    rubric_version: str | None = None


class MentionContext(StrictModel):
    """Where and how one mention sits inside an answer (deterministic; ADR 0021)."""

    prompt_id: str
    engine: Engine
    entity: str
    sentence: str
    container: str = Field(description="prose | list | table | heading")
    first_third: bool
    sourced_via_domain: str | None = Field(
        default=None, description="Domain of the source the engine attached to the sentence."
    )
    sourced_via_class: str | None = Field(default=None, description="classify_domain() bucket.")
    listed_with: int = Field(ge=0, description="Other items in the same list or table block.")
    polarity: str | None = Field(default=None, description="From the judge when scored.")
    captured_at: datetime


class DomainShare(StrictModel):
    """Share of answers citing a domain."""

    domain: str
    share: float = Field(ge=0.0, le=1.0)


class ActionEvidence(StrictModel):
    """Everything an analyst needs to trust a card."""

    quotes: list[EvidenceQuote] = Field(default_factory=list)
    domains: list[DomainShare] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    numbers: dict[str, float] = Field(default_factory=dict)


class ActionCard(StrictModel):
    """A prescribed action with its evidence and analyst state."""

    id: str
    type: str
    title: str
    prescription: str
    impact_score: float = Field(ge=0.0)
    engine: Engine | None = None
    subtopic: str
    prompt_ids: list[str] = Field(default_factory=list)
    evidence: ActionEvidence
    metric: str = Field(
        default="cited_rate", description="Number scored after the next consolidation."
    )
    status: str = Field(default="open", pattern="^(open|done)$")
    outcome: str = Field(
        default="pending", description="pending | improved | unchanged | regressed"
    )
    owner: str | None = None
    note: str | None = None


class FanoutQuery(StrictModel):
    """A sub-query an engine ran to answer the project's prompts."""

    query: str
    engines: list[Engine]
    prompts: int = Field(ge=1)
    subtopic: str
    client_covered: bool = Field(
        description="A client page was cited in an answer that ran this query."
    )


class ClaimEntry(StrictModel):
    """A sentence attributed by an engine to a source."""

    sentence: str
    url: str
    domain: str
    engine: Engine
    prompt_id: str
    is_client: bool
    is_competitor: bool


class TrustShare(StrictModel):
    """What kind of sites an engine cites for this project."""

    engine: Engine
    domain_class: str
    share: float = Field(ge=0.0, le=1.0)
    citations: int = Field(ge=0)


class RejectedPage(StrictModel):
    """A client page an engine consulted and did not cite."""

    url: str
    engine: Engine
    samples: int = Field(ge=1)
    queries: list[str] = Field(default_factory=list)


class PageInventory(StrictModel):
    """A cited page and how often it wins."""

    url: str
    domain: str
    title: str | None = None
    citations: int = Field(ge=1)
    engines: list[Engine]
    prompts: int = Field(ge=1)
    snippet: str | None = None
    date: str | None = None
    is_client: bool


class PlacementProfile(StrictModel):
    """Where the brand appears inside answers on one platform."""

    engine: Engine
    samples_with_mention: int = Field(ge=0)
    first_third: int = Field(ge=0)
    in_list: int = Field(ge=0)
    in_table: int = Field(ge=0)
    recommendation_sentence: int = Field(ge=0)


class FreshnessProfile(StrictModel):
    """Age of cited sources on one platform (vendor-dated sources only)."""

    engine: Engine
    dated_sources: int = Field(ge=0)
    median_age_days: float | None = None
    client_median_age_days: float | None = None
    competitor_median_age_days: float | None = None


class InsightsView(StrictModel):
    """Everything the Overview, Actions and Atlas pages show."""

    generated_at: datetime
    basis: InsightBasis
    health: list[EngineHealth] = Field(default_factory=list)
    changes: list[InsightChange] = Field(default_factory=list)
    actions: list[ActionCard] = Field(default_factory=list)
    fanout: list[FanoutQuery] = Field(default_factory=list)
    claims: list[ClaimEntry] = Field(default_factory=list)
    trust_profile: list[TrustShare] = Field(default_factory=list)
    read_but_rejected: list[RejectedPage] = Field(default_factory=list)
    winning_pages: list[PageInventory] = Field(default_factory=list)
    client_pages: list[PageInventory] = Field(default_factory=list)
    placement: list[PlacementProfile] = Field(default_factory=list)
    freshness: list[FreshnessProfile] = Field(default_factory=list)
    sentiment: list[SentimentProfile] = Field(default_factory=list)
    sentiment_coverage: SentimentCoverage = Field(
        default_factory=lambda: SentimentCoverage(configured=False, judged=0, unscored=0)
    )
    mention_context: list[MentionContext] = Field(default_factory=list)


class PromptResult(StrictModel):
    """Latest state of one prompt across the project's platforms."""

    prompt: TrackedPrompt
    effective_interval: str
    effective_engines: list[Engine]
    snapshots: dict[str, CitationSnapshot | None] = Field(default_factory=dict)
    organic_prompt: OrganicRankSnapshot | None = None
    organic_keyword: OrganicRankSnapshot | None = None
    due_on: list[Engine] = Field(default_factory=list)


class EngineStatus(StrEnum):
    """Why a prompt × platform cell is empty.

    Absence has three unrelated causes and they must not render alike: a platform
    the project does not track, one it tracks but has never asked, and one that was
    asked and failed every time (Gemini's billing 429s account for 41 such pairs).
    """

    HAS_DATA = "has_data"
    ASKED_FAILED = "asked_failed"
    NEVER_ASKED = "never_asked"
    NOT_CONFIGURED = "not_configured"


class PromptEngineDetail(StrictModel):
    """One platform's standing on one prompt, with the counts behind the verdict."""

    engine: Engine
    status: EngineStatus
    crawls: int = Field(default=0, ge=0, description="Snapshots stored for this pair.")
    samples: int = Field(default=0, ge=0)
    ok_samples: int = Field(default=0, ge=0, description="Samples that returned an answer.")
    failed_samples: int = Field(default=0, ge=0)
    cited_samples: int = Field(default=0, ge=0)
    citation_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Latest stored rate. Its denominator excludes failed samples and it "
        "is rounded at write time, so callers must display it rather than recompute it.",
    )
    mention_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    best_rank: int | None = Field(default=None, ge=1)
    cited: bool = Field(
        default=False,
        description="The stored majority verdict: cited in at least half the samples.",
    )
    cited_in_minority: bool = Field(
        default=False,
        description="Cited in at least one sample but below the majority threshold, so "
        "`cited` is False while the client genuinely holds a rank.",
    )
    models: list[str] = Field(
        default_factory=list,
        description="Distinct models behind the series, newest first. A change breaks "
        "trend comparability and should be annotated on the chart.",
    )
    history: list[CitationSnapshot] = Field(default_factory=list)
    velocity: VelocityReport | None = None


class PromptCapture(StrictModel):
    """Which rich-capture layers exist for this prompt, so empty tabs can say why.

    Capture landed in cycle 0011 and the split is per run: a prompt sampled only
    before it has no answer text at all, which is not the same as an answer with no
    citations.
    """

    samples: int = Field(default=0, ge=0)
    with_answer_text: int = Field(default=0, ge=0)
    with_search_queries: int = Field(default=0, ge=0)
    with_citation_claims: int = Field(default=0, ge=0)
    with_source_snippets: int = Field(default=0, ge=0)
    first_captured_at: datetime | None = None
    last_captured_at: datetime | None = None


class PromptPosition(StrictModel):
    """One consolidated position for this prompt, with its window for the x-axis."""

    consolidation_id: str
    consolidated_at: datetime
    window_runs: int = Field(ge=1)
    first_run_at: datetime | None = None
    last_run_at: datetime | None = None
    position: ConsolidatedPosition


class PromptDetail(StrictModel):
    """Everything the control plane knows about one tracked prompt.

    Insights are deliberately absent. For them, request `/insights?prompt_id=`,
    which applies the scope before the project-wide caps; filtering the unscoped
    response on the client loses rows for any prompt outside the top-N and
    mis-attributes claims shared between prompts (ADR 0017).
    """

    project_id: str
    lob: str
    result: PromptResult
    engines: list[PromptEngineDetail] = Field(default_factory=list)
    organic_prompt: list[OrganicRankSnapshot] = Field(default_factory=list)
    organic_keyword: list[OrganicRankSnapshot] = Field(default_factory=list)
    organic_prompt_velocity: OrganicVelocityReport | None = None
    organic_keyword_velocity: OrganicVelocityReport | None = None
    run_ids: list[str] = Field(
        default_factory=list,
        description="Runs that sampled this prompt, newest first. Built from the samples "
        "themselves, not the project's crawl list, which can omit them.",
    )
    positions: list[PromptPosition] = Field(default_factory=list)
    content_gap: bool = Field(
        default=False, description="No landing page maps to this prompt's subtopic."
    )
    capture: PromptCapture = Field(default_factory=PromptCapture)
    shared_lob_projects: list[str] = Field(
        default_factory=list,
        description="Other projects on the same line of business. Prompt history is keyed "
        "by (lob, text), so identical prompts in these projects share one series.",
    )
