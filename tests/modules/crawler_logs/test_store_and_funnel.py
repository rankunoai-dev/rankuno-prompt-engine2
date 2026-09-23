"""Persistence semantics (per-import rows, duplicates, per-day winners, purge) and the
fetch-to-citation funnel over samples written through `TimeSeriesDB`."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from src.integrations.schemas import Citation, Engine
from src.modules.crawler_logs.funnel import build_view
from src.modules.crawler_logs.ingest import aggregate
from src.modules.crawler_logs.parser import ParseStats, iter_hits
from src.modules.crawler_logs.ranges import BotRanges
from src.modules.crawler_logs.store import CrawlerLogStore, DuplicateImport
from src.modules.prompt_tracking.schemas import (
    AnswerSample,
    DecisionStage,
    MasterPromptRecord,
    PromptType,
    SearchIntent,
    Verdict,
)
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB

TODAY = date(2026, 9, 20)
NOW = datetime(2026, 9, 18, 6, 59, 57, tzinfo=UTC)
RANGES = BotRanges(
    {
        "vendors": {
            "openai": {"sources": [{"prefixes": ["203.0.113.0/24"]}]},
            "perplexity": {"sources": [{"prefixes": ["198.51.100.0/24"]}]},
        }
    }
)
SEARCH = "Mozilla/5.0 (compatible; OAI-SearchBot/1.0)"
GOOGLE = "Mozilla/5.0 (compatible; Googlebot/2.1)"
PPLX = "Mozilla/5.0 (compatible; PerplexityBot/1.0)"


def line(
    path: str,
    ua: str = SEARCH,
    ip: str = "203.0.113.9",
    status: int = 200,
    day: int = 18,
    sec: int = 57,
) -> str:
    when = f"[{day}/Sep/2026:06:59:{sec:02d} +0000]"
    return f'{ip} - - {when} "GET {path} HTTP/1.1" {status} 512 "-" "{ua}"'


def lines(n: int, path: str, **kw: object) -> list[str]:
    """`n` distinct request lines for one page (identical lines would dedupe)."""
    return [line(path, sec=i, **kw) for i in range(n)]  # type: ignore[arg-type]


def ingest(store: CrawlerLogStore, project_id: str, text: str, *, now: datetime | None = None):
    stats = ParseStats()
    hits = iter_hits([text.encode()], stats, max_bytes=10_000_000, max_json_bytes=1_000_000)
    result = aggregate(hits, client_domains=["gep.com"], ranges=RANGES, stats=stats)
    return store.record_import(project_id, result, now=now)


@pytest.fixture
def store(tmp_path):
    return CrawlerLogStore(tmp_path / "t.sqlite")


# -- store ----------------------------------------------------------------------------


def test_duplicate_content_is_refused_and_imports_carry_overlaps(store):
    text = line("/blog/x") + "\n" + line("/blog/y") + "\n"
    first = ingest(store, "p1", text)
    with pytest.raises(DuplicateImport) as info:
        ingest(store, "p1", text)
    assert info.value.import_id == first.id
    ingest(store, "p2", text)  # the same file is fine in another project
    second = ingest(store, "p1", line("/blog/z") + "\n")  # same day: overlap
    listed = {i.id: i for i in store.imports("p1")}
    assert listed[first.id].overlaps == [second.id] and listed[second.id].overlaps == [first.id]
    assert listed[first.id].span_from == date(2026, 9, 18) and listed[first.id].format == "combined"


def test_per_day_winner_is_most_lines_then_newest(store):
    big = ingest(store, "p1", "\n".join(line(f"/a{i}") for i in range(5)) + "\n", now=NOW)
    ingest(store, "p1", line("/b", day=18) + "\n", now=NOW + timedelta(hours=1))
    rows = store.hits("p1", date(2026, 9, 18), date(2026, 9, 18))
    assert {r.url_key for r in rows} == {f"gep.com/a{i}" for i in range(5)}
    assert store.winners("p1", date(2026, 9, 18), date(2026, 9, 18)) == {"2026-09-18": big.id}
    # a different day from the small import still counts
    ingest(store, "p1", line("/c", day=19) + "\n")
    assert {r.url_key for r in store.hits("p1", date(2026, 9, 18), date(2026, 9, 19))} >= {
        "gep.com/c"
    }


def test_delete_and_purge(store):
    old = ingest(store, "p1", line("/old", day=1) + "\n")
    new = ingest(store, "p1", line("/new", day=19) + "\n")
    removed = store.purge(10, today=TODAY)  # cutoff 2026-09-10: the 1st goes, the 19th stays
    assert removed == 1
    recs = {i.id: i for i in store.imports("p1")}
    assert recs[old.id].purged_at is not None and recs[new.id].purged_at is None
    assert [r.url_key for r in store.hits("p1", date(2026, 9, 1), TODAY)] == ["gep.com/new"]
    store.delete_import("p1", new.id)
    with pytest.raises(KeyError):
        store.delete_import("p1", new.id)
    ingest(store, "p1", line("/again", day=19) + "\n")
    store.delete_project_data("p1")
    assert store.imports("p1") == []


# -- funnel ---------------------------------------------------------------------------


def _seed_samples(db: TimeSeriesDB) -> list[str]:
    pid = "a" * 16
    db.upsert_prompt(
        MasterPromptRecord(
            prompt_id=pid,
            lob="Procurement Software",
            subtopic="S",
            core_keyword="k",
            search_volume=10,
            prompt_text="p",
            search_intent=SearchIntent.COMMERCIAL,
            decision_stage=DecisionStage.CONSIDERATION,
            prompt_type=PromptType.NON_BRANDED,
            web_triggers=True,
            verdict=Verdict.KEEP,
            verdict_reason="x",
        ),
        "GEP",
    )

    def sample(
        engine: Engine, cited: list[str], consulted: list[str], resolved: bool = True
    ) -> AnswerSample:
        return AnswerSample(
            prompt_id=pid,
            engine=engine,
            model="m",
            captured_at=NOW,
            web_triggered=True,
            client_cited=bool(cited),
            citation_links=[
                Citation(url=u, domain="gep.com", title="t", position=i + 1, resolved=resolved)
                for i, u in enumerate(cited)
            ],
            consulted_urls=consulted,
            search_queries=["gep smart erp integration"],
        )

    chat = Engine.CHATGPT_SEARCH
    db.record_samples(
        pid,
        [
            sample(
                chat,
                ["https://www.gep.com/software/gep-smart?utm_source=openai"],
                ["https://gep.com/blog/read-me/"],
            ),
            sample(
                chat, ["https://www.gep.com/software/gep-smart/"], ["https://gep.com/blog/read-me"]
            ),
            sample(chat, [], []),
            sample(
                Engine.GEMINI,
                ["https://vertexaisearch.cloud.google.com/redirect/abc"],
                [],
                resolved=False,
            ),
            sample(Engine.PERPLEXITY, ["https://gep.com/Blog/Case-Study"], []),
        ],
        "run-1",
    )
    return [pid]


def test_funnel_joins_fetches_to_citations_and_emits_cards(tmp_path):
    db = TimeSeriesDB(tmp_path / "f.sqlite")
    store = CrawlerLogStore(db.path)
    prompt_ids = _seed_samples(db)
    log = "\n".join(
        [
            *lines(3, "/software/gep-smart/index.html"),  # fetched AND cited (exact)
            *lines(4, "/blog/read-me"),  # fetched, consulted twice, never cited
            *lines(3, "/blog/never"),  # fetched, never consulted, never cited
            *lines(3, "/blog/case-study"),  # near miss: cited as /Blog/Case-Study
            *lines(5, "/blog/blocked", status=403),  # blocked: no card
            *lines(4, "/moved", status=301),  # redirected only
            *lines(6, "/assets/app.css"),  # asset: never a card
            *lines(9, "/blog/never", ua=GOOGLE),  # Googlebot never drives a card
            *lines(3, "/blog/pplx", ua=PPLX, ip="10.0.0.1"),  # engine under the sample floor
        ]
    )
    ingest(store, "proj", log + "\n")
    view = build_view(
        project_id="proj",
        client_domains=["gep.com"],
        engines=[Engine.CHATGPT_SEARCH, Engine.PERPLEXITY, Engine.GEMINI],
        prompt_ids=prompt_ids,
        store=store,
        db=db,
        days=30,
        today=TODAY,
    )
    pages = {p.url_key: p for p in view.pages}
    smart = pages["gep.com/software/gep-smart"]
    assert (
        smart.match == "exact" and smart.cited == {"CHATGPT_SEARCH": 2} and smart.last_cited == NOW
    )
    read = pages["gep.com/blog/read-me"]
    assert read.match == "none" and read.consulted == 2 and read.ok_fetches == 4
    assert pages["gep.com/blog/case-study"].match == "near"
    assert pages["gep.com/blog/case-study"].near_urls == ["https://gep.com/Blog/Case-Study"]
    assert pages["gep.com/moved"].redirected_only and pages["gep.com/assets/app.css"].is_asset
    assert view.covered_days == 1 and view.daily[0].hits == 40
    bots = {b.bot: b for b in view.by_bot}
    assert bots["OAI-SearchBot"].verified_hits == 28 and bots["PerplexityBot"].verified_hits == 0
    assert bots["Googlebot"].verified_hits is None

    cards = {(c.url_key, c.bot): c for c in view.fetched_not_cited}
    assert set(cards) == {
        ("gep.com/blog/read-me", "OAI-SearchBot"),
        ("gep.com/blog/never", "OAI-SearchBot"),
    }
    assert cards[("gep.com/blog/read-me", "OAI-SearchBot")].consulted == 2
    assert cards[("gep.com/blog/read-me", "OAI-SearchBot")].queries == ["gep smart erp integration"]
    assert cards[("gep.com/blog/never", "OAI-SearchBot")].consulted == 0
    # Perplexity has one sample in the window: below the floor, so no card even at 3 fetches


def test_funnel_without_any_log_is_empty_but_well_formed(tmp_path):
    db = TimeSeriesDB(tmp_path / "e.sqlite")
    view = build_view(
        project_id="proj",
        client_domains=["gep.com"],
        engines=[Engine.CHATGPT_SEARCH],
        prompt_ids=[],
        store=CrawlerLogStore(db.path),
        db=db,
        days=7,
        today=TODAY,
    )
    assert view.pages == [] and view.covered_days == 0 and view.since == TODAY - timedelta(days=6)
