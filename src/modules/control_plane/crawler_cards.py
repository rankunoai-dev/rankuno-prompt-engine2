"""Action cards from the crawler-log funnel (ADR 0022).

Kept out of `insights.py`, which is already past the size target; the engine
takes these through its `extra_cards` hook and applies analyst state to them.
"""

from __future__ import annotations

from src.modules.control_plane.insights import action_id
from src.modules.control_plane.schemas import ActionCard, ActionEvidence, Project, TrackedPrompt
from src.modules.crawler_logs.bots import LIVE_FETCH
from src.modules.crawler_logs.funnel import build_view
from src.modules.crawler_logs.schemas import FetchedNotCited
from src.modules.crawler_logs.store import CrawlerLogStore
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB

__all__ = ["CARD_TYPE", "WINDOW_DAYS", "card_from", "cards_for"]

CARD_TYPE = "fetched_not_cited"
WINDOW_DAYS = 30
_TITLE = "{bot} fetched {page} {n}x in {days} days; {engine} never cited it"
_READ_AND_PASSED = (
    "{engine} read this page in {consulted} answer(s) and cited other sources every time: "
    "the page was found and judged wanting. Open the answers that consulted it, put the "
    "missing specifics in the first 100 words, and refresh the visible date."
)
_INDEXED_ONLY = (
    "{bot} keeps fetching this page ({ok} successful fetches{verified}) but it never "
    "reached an answer as a citation. The crawl is not the problem. Check the page answers "
    "the tracked prompts directly, carries one clear claim per heading, and is not "
    "duplicated under another URL."
)


def card_from(row: FetchedNotCited) -> ActionCard:
    """One card per (page, bot). Ids are stable across rebuilds like every other card."""
    page = row.url_key.split("/", 1)[1] if "/" in row.url_key else row.url_key
    page = "/" + page[:60]
    verified = ""
    if row.verified is not None:
        verified = f", {row.verified} from {row.bot}'s published addresses"
    fmt = {
        "bot": row.bot,
        "page": page,
        "n": row.fetches,
        "days": row.days,
        "engine": row.engine.value,
        "consulted": row.consulted or 0,
        "ok": row.fetches,
        "verified": verified,
    }
    prescription = _READ_AND_PASSED if row.consulted else _INDEXED_ONLY
    impact = (1.0 + 0.2 * row.fetches) * (1.5 if row.purpose == LIVE_FETCH else 1.0)
    numbers: dict[str, float] = {
        "fetches": float(row.fetches),
        "blocked": float(row.blocked),
        "days": float(row.days),
        "cited_rate": 0.0,
    }
    if row.verified is not None:
        numbers["verified_fetches"] = float(row.verified)
    if row.consulted is not None:
        numbers["consulted"] = float(row.consulted)
    return ActionCard(
        id=action_id(CARD_TYPE, row.engine.value, "Crawl", row.url_key),
        type=CARD_TYPE,
        title=_TITLE.format(**fmt),
        prescription=prescription.format(**fmt),
        impact_score=round(min(impact, 12.0), 3),
        engine=row.engine,
        subtopic="Crawl",
        prompt_ids=[],
        evidence=ActionEvidence(
            urls=[f"https://{row.url_key}"], queries=row.queries, numbers=numbers
        ),
        metric="cited_rate",
        status="open",
        outcome="pending",
        owner=None,
        note=None,
    )


def cards_for(
    project: Project,
    prompts: list[TrackedPrompt],
    store: CrawlerLogStore,
    db: TimeSeriesDB,
    *,
    days: int = WINDOW_DAYS,
) -> list[ActionCard]:
    """Cards for a project's current window; empty when no log was ever imported."""
    if not store.imports(project.id):
        return []
    view = build_view(
        project_id=project.id,
        client_domains=list(project.client.domains),
        engines=list(project.engines),
        prompt_ids=[p.prompt_id for p in prompts],
        store=store,
        db=db,
        days=days,
    )
    return [card_from(row) for row in view.fetched_not_cited]
