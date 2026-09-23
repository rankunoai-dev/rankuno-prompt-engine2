"""Contracts for the executive report (ADR 0024).

Three layers, deliberately separate:

* **FactSheet** — everything the report states, computed from stored data by
  `facts.py`. Numbers only: no engine prose, no raw answer text.
* **Narrative** — the words wrapped around those numbers. Written by a model
  or by a template, and marked with which.
* **ReportRecord** — one generation attempt, its state and its artefact.

The split is what makes the narrative safe to delegate: the model sees a
FactSheet and may only restate it, and every number it writes is checked back
against the same object before the PDF is drawn.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from pydantic import Field

from src.core.branding import Brand
from src.core.locale import Locale
from src.core.schemas import StrictModel
from src.integrations.schemas import Engine

__all__ = [
    "ActionFact",
    "ChangeFact",
    "CompetitorFact",
    "EngineFact",
    "FactSheet",
    "Kpi",
    "Narrative",
    "NarrativeSource",
    "PageFact",
    "ReportRecord",
    "ReportRequest",
    "ReportState",
    "SentimentFact",
    "WindowFact",
]

MAX_ACTIONS: Final = 8
MAX_CHANGES: Final = 12
MAX_PAGES: Final = 8
MAX_COMPETITORS: Final = 8


class ReportState(StrEnum):
    """Where one generation attempt got to."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class NarrativeSource(StrEnum):
    """Who wrote the prose."""

    MODEL = "model"
    """A vendor model wrote it and every number survived the fact check."""

    TEMPLATE = "template"
    """Deterministic sentences: no key, no budget, a refusal, or a failed check."""

    MIXED = "mixed"
    """Some sections came back clean; the rest fell back to templates."""


class Kpi(StrictModel):
    """One headline number, with its band and its previous value."""

    key: str = Field(description="Stable identifier, e.g. 'citation_rate'.")
    label: str
    value: float
    unit: str = Field(default="percent", description="percent | count | rank")
    low: float | None = Field(default=None, description="Lower bound of the 95% band.")
    high: float | None = None
    previous: float | None = Field(default=None, description="Same metric, previous window.")
    delta: float | None = Field(default=None, description="value - previous, when both exist.")
    note: str = ""


class EngineFact(StrictModel):
    """One platform's standing in the window."""

    engine: Engine
    label: str
    verdict: str = Field(description="winning | present | invisible | losing")
    losing_to: str | None = None
    cited_rate: float = Field(ge=0.0, le=1.0)
    cited_rate_low: float | None = None
    cited_rate_high: float | None = None
    mention_rate: float = Field(ge=0.0, le=1.0)
    best_rank: int | None = None
    delta_cited_rate: float | None = None
    volatility: float = Field(ge=0.0, le=1.0)
    prompts: int = Field(ge=0)
    samples: int = Field(ge=0)
    crawls: int = Field(ge=0)
    honours_locale: bool = Field(
        description="False for engines whose API takes no location (ADR 0023)."
    )


class ChangeFact(StrictModel):
    """One movement since the previous window."""

    kind: str
    prompt_text: str
    engine: Engine
    before: str
    after: str
    text: str


class ActionFact(StrictModel):
    """One recommendation, with the evidence a client can act on."""

    title: str
    prescription: str
    impact_score: float = Field(ge=0.0)
    engine: Engine | None = None
    subtopic: str
    metric: str
    urls: list[str] = Field(default_factory=list, max_length=4)
    status: str = Field(default="open")


class CompetitorFact(StrictModel):
    """A rival's share of the citations in the window."""

    domain: str
    share: float = Field(ge=0.0, le=1.0)
    is_client: bool = False


class PageFact(StrictModel):
    """A page the engines actually cited."""

    url: str
    domain: str
    title: str | None = None
    citations: int = Field(ge=1)
    engines: list[Engine] = Field(default_factory=list)
    is_client: bool


class SentimentFact(StrictModel):
    """How the engines describe one entity (ADR 0021)."""

    engine: Engine
    entity: str
    judged: int = Field(ge=0)
    negative_share: float = Field(ge=0.0, le=1.0)
    negative_share_low: float | None = None
    negative_share_high: float | None = None
    attributes: list[str] = Field(default_factory=list, max_length=3)
    worst_quote: str | None = None
    worst_url: str | None = None


class WindowFact(StrictModel):
    """What the report covers."""

    consolidation_id: str | None = None
    computed_from: str = Field(description="consolidation | latest_crawl | none")
    first_run_at: datetime | None = None
    last_run_at: datetime | None = None
    crawls: int = Field(ge=0)
    prompts: int = Field(ge=0)
    samples: int = Field(ge=0)
    low_confidence: bool = False
    previous_consolidation_id: str | None = None


class FactSheet(StrictModel):
    """Every number the report is allowed to state."""

    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    project_id: str
    project_name: str
    client_brand: str
    client_domain: str
    lob: str
    locale: Locale | None = None
    locale_label: str = Field(default="Server default")
    engines_without_locale: list[str] = Field(
        default_factory=list, description="Engine labels that ignore the market (ADR 0023)."
    )
    window: WindowFact
    kpis: list[Kpi] = Field(default_factory=list)
    engines: list[EngineFact] = Field(default_factory=list)
    changes: list[ChangeFact] = Field(default_factory=list, max_length=MAX_CHANGES)
    actions: list[ActionFact] = Field(default_factory=list, max_length=MAX_ACTIONS)
    competitors: list[CompetitorFact] = Field(default_factory=list, max_length=MAX_COMPETITORS)
    client_pages: list[PageFact] = Field(default_factory=list, max_length=MAX_PAGES)
    winning_pages: list[PageFact] = Field(default_factory=list, max_length=MAX_PAGES)
    sentiment: list[SentimentFact] = Field(default_factory=list)
    sentiment_configured: bool = False
    spend_usd: float | None = Field(
        default=None, description="Vendor spend for the window; only when the brand opts in."
    )

    @property
    def headline_rate(self) -> float:
        """Citation rate across the window, the number the cover leads with."""
        for kpi in self.kpis:
            if kpi.key == "citation_rate":
                return kpi.value
        return 0.0


class Narrative(StrictModel):
    """The prose around the numbers."""

    headline: str = Field(max_length=120)
    summary: str = Field(max_length=1600)
    wins: list[str] = Field(default_factory=list, max_length=3)
    risks: list[str] = Field(default_factory=list, max_length=3)
    next_steps: list[str] = Field(default_factory=list, max_length=3)
    source: NarrativeSource = NarrativeSource.TEMPLATE
    model: str | None = None
    rejected_sections: list[str] = Field(
        default_factory=list,
        description="Sections the fact check refused and replaced with a template.",
    )
    spend_usd: float = Field(default=0.0, ge=0.0)


class ReportRequest(StrictModel):
    """What the operator asked for."""

    consolidation_id: str | None = Field(
        default=None, description="Window to report on; the latest when unset."
    )
    title: str | None = Field(default=None, max_length=120)
    narrative: bool = Field(
        default=True, description="Ask the model for prose; templates are used when False."
    )
    include_actions: bool = True
    include_sentiment: bool = True
    include_pages: bool = True
    email_to: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Deliver the finished PDF to these addresses. Requires SMTP settings.",
    )


class ReportRecord(StrictModel):
    """One generation attempt."""

    id: str = Field(min_length=8)
    project_id: str = Field(min_length=8)
    state: ReportState = ReportState.QUEUED
    request: ReportRequest
    brand: Brand
    title: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    file_name: str | None = Field(default=None, description="Inside the project's report folder.")
    size_bytes: int | None = Field(default=None, ge=0)
    pages: int | None = Field(default=None, ge=0)
    narrative_source: NarrativeSource | None = None
    narrative_model: str | None = None
    spend_usd: float = Field(default=0.0, ge=0.0)
    window_label: str = ""
    emailed_to: int = Field(default=0, ge=0, description="Recipients the PDF reached.")
    error: str | None = None
    purged_at: datetime | None = None

    @property
    def downloadable(self) -> bool:
        """True when a file exists to serve."""
        return self.state is ReportState.DONE and self.file_name is not None and not self.purged_at
