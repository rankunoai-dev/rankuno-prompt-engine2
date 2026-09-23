"""Fetch → Consulted → Cited, per client page, for one project and window.

The join is done in Python because the key on both sides is `url_key`, which
percent-decodes, normalises and strips index files — nothing SQLite can do.
Citations are scoped to the project's own prompts and engines, and the window
is the intersection of the days asked for and the days a log actually covers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from src.core.domains import domain_matches, normalize_domain
from src.integrations.schemas import Engine
from src.modules.crawler_logs.bots import INDEX, LIVE_FETCH, spec_for
from src.modules.crawler_logs.normalise import is_asset, url_key_ci, url_key_from_url
from src.modules.crawler_logs.schemas import (
    BotSummary,
    CrawlerDay,
    CrawlerLogView,
    FetchedNotCited,
    FunnelPage,
    PageFetches,
)
from src.modules.crawler_logs.store import CrawlerLogStore, HitRow
from src.modules.prompt_tracking.schemas import AnswerSample
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB

__all__ = ["MIN_FETCHES", "MIN_SAMPLES", "build_view"]

MIN_FETCHES = 3
MIN_SAMPLES = 3
_MAX_PAGES = 500


@dataclass
class _Cited:
    per_engine: dict[str, int] = field(default_factory=dict)
    last: datetime | None = None
    urls: set[str] = field(default_factory=set)


@dataclass
class _Engineside:
    cited: dict[str, _Cited] = field(default_factory=dict)  # url_key -> counts
    consulted: dict[str, int] = field(default_factory=dict)  # url_key -> answers
    consulted_queries: dict[str, set[str]] = field(default_factory=dict)
    ci_index: dict[str, set[str]] = field(default_factory=dict)  # casefold -> keys
    samples_by_engine: dict[str, int] = field(default_factory=dict)
    consulting_engines: set[str] = field(default_factory=set)


def _engine_side(
    samples: list[AnswerSample], client_domains: list[str], engines: list[Engine]
) -> _Engineside:
    side = _Engineside()
    wanted = {e.value for e in engines}
    domains = [normalize_domain(d) for d in client_domains if d]
    for s in samples:
        if s.engine.value not in wanted:
            continue
        side.samples_by_engine[s.engine.value] = side.samples_by_engine.get(s.engine.value, 0) + 1
        for link in s.citation_links:
            if not link.resolved or not any(domain_matches(link.domain, d) for d in domains):
                continue
            key = url_key_from_url(link.url)
            entry = side.cited.setdefault(key, _Cited())
            entry.per_engine[s.engine.value] = entry.per_engine.get(s.engine.value, 0) + 1
            entry.urls.add(link.url)
            if entry.last is None or s.captured_at > entry.last:
                entry.last = s.captured_at
            side.ci_index.setdefault(url_key_ci(key), set()).add(key)
        if s.consulted_urls:
            side.consulting_engines.add(s.engine.value)
        for url in s.consulted_urls:
            key = url_key_from_url(url)
            if not any(domain_matches(key.split("/", 1)[0], d) for d in domains):
                continue
            side.consulted[key] = side.consulted.get(key, 0) + 1
            side.consulted_queries.setdefault(key, set()).update(s.search_queries[:5])
    return side


def build_view(
    *,
    project_id: str,
    client_domains: list[str],
    engines: list[Engine],
    prompt_ids: list[str],
    store: CrawlerLogStore,
    db: TimeSeriesDB,
    days: int,
    today: date | None = None,
) -> CrawlerLogView:
    """Everything the Crawler logs tab shows for `days` up to today."""
    until = today or datetime.now(UTC).date()
    since = until - timedelta(days=days - 1)
    rows = store.hits(project_id, since, until)
    covered = store.winners(project_id, since, until)
    samples = db.samples_since(prompt_ids, datetime.combine(since, datetime.min.time(), tzinfo=UTC))
    side = _engine_side(samples, client_domains, engines)

    pages: dict[str, dict[str, PageFetches]] = {}
    by_bot: dict[str, BotSummary] = {}
    daily: dict[str, CrawlerDay] = {}
    for r in rows:
        spec = spec_for(r.bot)
        if spec is None:
            continue
        _fold_page(pages, r, spec.purpose)
        _fold_bot(by_bot, r, spec.vendor, spec.purpose, spec.engine)
        day = daily.setdefault(
            r.day, CrawlerDay(day=date.fromisoformat(r.day), covered=True, hits=0)
        )
        day.hits += r.hits
        day.by_bot[r.bot] = day.by_bot.get(r.bot, 0) + r.hits
    for d in covered:
        daily.setdefault(d, CrawlerDay(day=date.fromisoformat(d), covered=True, hits=0))
    for summary in by_bot.values():
        summary.pages = sum(1 for p in pages.values() if summary.bot in p)

    funnel = [_page(key, fetches, side) for key, fetches in pages.items()]
    funnel.sort(key=lambda p: -p.total_fetches)
    cards = _fetched_not_cited(funnel, side, days)
    return CrawlerLogView(
        days=days,
        since=since,
        until=until,
        covered_days=len(covered),
        by_bot=sorted(by_bot.values(), key=lambda b: -b.hits),
        daily=[daily[d] for d in sorted(daily)],
        pages=funnel[:_MAX_PAGES],
        stealth=store.stealth(project_id, since, until),
        fetched_not_cited=cards,
        imports=store.imports(project_id),
    )


def _fold_page(pages: dict[str, dict[str, PageFetches]], r: HitRow, purpose: str) -> None:
    per_bot = pages.setdefault(r.url_key, {})
    pf = per_bot.get(r.bot)
    if pf is None:
        pf = PageFetches(bot=r.bot, purpose=purpose, hits=0, ok=0, blocked=0, redirected=0)
        per_bot[r.bot] = pf
    pf.hits += r.hits
    pf.ok += r.s2xx + r.s304
    pf.blocked += r.blocked
    pf.redirected += r.s3xx
    if r.verified is not None:
        pf.verified = (pf.verified or 0) + r.verified
    if r.last_seen and (pf.last_seen is None or r.last_seen > pf.last_seen):
        pf.last_seen = r.last_seen


def _fold_bot(
    by_bot: dict[str, BotSummary], r: HitRow, vendor: str, purpose: str, engine: Engine | None
) -> None:
    b = by_bot.get(r.bot)
    if b is None:
        b = BotSummary(
            bot=r.bot, vendor=vendor, purpose=purpose, engine=engine, hits=0, blocked=0, pages=0
        )
        by_bot[r.bot] = b
    b.hits += r.hits
    b.blocked += r.blocked
    if r.verified is not None:
        b.verified_hits = (b.verified_hits or 0) + r.verified
    if r.last_seen and (b.last_seen is None or r.last_seen > b.last_seen):
        b.last_seen = r.last_seen


def _page(key: str, fetches: dict[str, PageFetches], side: _Engineside) -> FunnelPage:
    cited = side.cited.get(key)
    match, near = "exact", []
    if cited is None:
        siblings = side.ci_index.get(url_key_ci(key), set()) - {key}
        if siblings:
            match, near = "near", sorted(u for k in siblings for u in side.cited[k].urls)[:5]
        else:
            match = "none"
    consulted = side.consulted.get(key, 0) if side.consulting_engines else None
    total = sum(f.hits for f in fetches.values())
    ok = sum(f.ok for f in fetches.values())
    return FunnelPage(
        url_key=key,
        fetches=sorted(fetches.values(), key=lambda f: -f.hits),
        total_fetches=total,
        ok_fetches=ok,
        consulted=consulted,
        cited=dict(cited.per_engine) if cited else {},
        last_fetch=max((f.last_seen for f in fetches.values() if f.last_seen), default=None),
        last_cited=cited.last if cited else None,
        match=match,
        near_urls=near,
        is_asset=is_asset(key.split("/", 1)[1] if "/" in key else "/"),
        redirected_only=ok == 0 and any(f.redirected for f in fetches.values()),
    )


def _fetched_not_cited(
    pages: list[FunnelPage], side: _Engineside, days: int
) -> list[FetchedNotCited]:
    out: list[FetchedNotCited] = []
    for page in pages:
        if page.is_asset or page.match != "none":
            continue
        for f in page.fetches:
            spec = spec_for(f.bot)
            if spec is None or spec.engine is None or spec.purpose not in (INDEX, LIVE_FETCH):
                continue
            if f.ok < MIN_FETCHES:
                continue
            if side.samples_by_engine.get(spec.engine.value, 0) < MIN_SAMPLES:
                continue
            consulted = (
                side.consulted.get(page.url_key, 0)
                if spec.engine.value in side.consulting_engines
                else None
            )
            out.append(
                FetchedNotCited(
                    url_key=page.url_key,
                    bot=f.bot,
                    purpose=f.purpose,
                    engine=spec.engine,
                    fetches=f.ok,
                    verified=f.verified,
                    blocked=f.blocked,
                    consulted=consulted,
                    days=days,
                    queries=sorted(side.consulted_queries.get(page.url_key, set()))[:8],
                )
            )
    out.sort(key=lambda c: (-(c.purpose == LIVE_FETCH), -c.fetches))
    return out[:20]
