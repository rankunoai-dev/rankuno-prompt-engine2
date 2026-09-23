"""Contracts for crawler-log imports and the fetch-to-citation funnel."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import Field

from src.core.schemas import StrictModel
from src.integrations.schemas import Engine

__all__ = [
    "BotSpecOut",
    "BotSummary",
    "CrawlerDay",
    "CrawlerImportRecord",
    "CrawlerImportResult",
    "CrawlerLogView",
    "FetchedNotCited",
    "FunnelPage",
    "PageFetches",
    "RangesSnapshot",
]


class BotSpecOut(StrictModel):
    """One catalogued crawler, as the UI shows it and pre-filters with it."""

    name: str
    token: str = Field(description="Substring that identifies the bot in a user agent.")
    vendor: str
    purpose: str = Field(pattern="^(training|index|live_fetch)$")
    engine: Engine | None = None
    verifiable: bool = Field(description="The vendor publishes IP ranges we bundle.")


class RangesSnapshot(StrictModel):
    """When the bundled IP ranges were taken, per vendor."""

    fetched_at: datetime | None = None
    vendors: dict[str, str] = Field(
        default_factory=dict, description="Vendor -> newest `creationTime` among its lists."
    )


class CrawlerImportResult(StrictModel):
    """What one upload contained and, explicitly, what was and was not kept."""

    import_id: str
    format: str = Field(pattern="^(combined|cloudflare)$")
    lines: int = Field(ge=0)
    parsed: int = Field(ge=0)
    unparsed: int = Field(ge=0)
    duplicate_lines: int = Field(ge=0)
    matched: int = Field(ge=0, description="Parsed lines attributed to a catalogued crawler.")
    hits: int = Field(ge=0, description="Crawler fetches kept, after host and method filters.")
    verified_hits: int = Field(ge=0)
    stealth_hits: int = Field(ge=0, description="In-range IP with no crawler user agent.")
    hosts_skipped: int = Field(ge=0)
    no_host: int = Field(ge=0, description="Records with no host field; assumed the project's.")
    methods_skipped: int = Field(ge=0)
    sensitive_dropped: int = Field(ge=0, description="Paths that looked like tokens or emails.")
    keys_truncated: bool = False
    span_from: date | None = None
    span_to: date | None = None
    verification_basis: str = Field(pattern="^(remote_addr|xff|cloudflare|none)$")
    sampled: bool = False
    overlaps: list[str] = Field(default_factory=list, description="Earlier imports sharing days.")
    stored: str = Field(
        default=(
            "Per-day counts per crawler and page only. No request lines, IP addresses, "
            "referers or query strings were stored; timestamps of user-triggered fetches "
            "are rounded to the minute."
        )
    )


class CrawlerImportRecord(StrictModel):
    """Provenance of one upload."""

    id: str
    imported_at: datetime
    format: str
    lines: int
    parsed: int
    matched: int
    span_from: date | None
    span_to: date | None
    verification_basis: str
    sampled: bool
    note: str
    purged_at: datetime | None = None
    overlaps: list[str] = Field(default_factory=list)


class BotSummary(StrictModel):
    """One crawler's activity in the window."""

    bot: str
    vendor: str
    purpose: str
    engine: Engine | None = None
    hits: int = Field(ge=0)
    verified_hits: int | None = Field(default=None, description="None when unverifiable.")
    blocked: int = Field(ge=0)
    pages: int = Field(ge=0)
    last_seen: datetime | None = None


class CrawlerDay(StrictModel):
    """Fetches on one day, with whether a log covered it at all."""

    day: date
    covered: bool
    hits: int = Field(ge=0)
    by_bot: dict[str, int] = Field(default_factory=dict)


class PageFetches(StrictModel):
    """One crawler's fetches of one page."""

    bot: str
    purpose: str
    hits: int = Field(ge=0)
    ok: int = Field(ge=0, description="2xx and 304.")
    blocked: int = Field(ge=0)
    redirected: int = Field(ge=0)
    verified: int | None = None
    last_seen: datetime | None = None


class FunnelPage(StrictModel):
    """Fetch → Consulted → Cited for one client page."""

    url_key: str
    fetches: list[PageFetches]
    total_fetches: int = Field(ge=0)
    ok_fetches: int = Field(ge=0)
    consulted: int | None = Field(
        default=None, description="Answers that read the page; None where the engine reports none."
    )
    cited: dict[str, int] = Field(default_factory=dict, description="Citations per engine.")
    last_fetch: datetime | None = None
    last_cited: datetime | None = None
    match: str = Field(default="exact", pattern="^(exact|near|none)$")
    near_urls: list[str] = Field(default_factory=list)
    is_asset: bool = False
    redirected_only: bool = False


class FetchedNotCited(StrictModel):
    """A page a search or live-fetch crawler read repeatedly that its engine never cited."""

    url_key: str
    bot: str
    purpose: str
    engine: Engine
    fetches: int = Field(ge=1)
    verified: int | None = None
    blocked: int = Field(ge=0)
    consulted: int | None = None
    days: int = Field(ge=1)
    queries: list[str] = Field(default_factory=list)


class CrawlerLogView(StrictModel):
    """Everything the Crawler logs tab shows for one window."""

    days: int
    since: date
    until: date
    covered_days: int = Field(ge=0)
    by_bot: list[BotSummary] = Field(default_factory=list)
    daily: list[CrawlerDay] = Field(default_factory=list)
    pages: list[FunnelPage] = Field(default_factory=list)
    stealth: dict[str, int] = Field(default_factory=dict)
    fetched_not_cited: list[FetchedNotCited] = Field(default_factory=list)
    imports: list[CrawlerImportRecord] = Field(default_factory=list)
    ranges: RangesSnapshot = Field(default_factory=RangesSnapshot)
