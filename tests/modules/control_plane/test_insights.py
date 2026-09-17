"""Insight engine: verdicts, changes, action cards, fan-out, claims, trust, pages, outcomes."""

from __future__ import annotations

from datetime import timedelta

import pytest

from src.integrations.schemas import Citation, CitationClaim, Engine, SourceSnippet
from src.modules.control_plane.actions import ActionStateStore
from src.modules.control_plane.insights import InsightEngine, classify_domain
from src.modules.control_plane.positioning import PositionStore
from src.modules.control_plane.schemas import ActionUpdate, ProjectRunRecord, TrackedPromptUpdate
from src.modules.prompt_tracking.schemas import (
    AnswerSample,
    CitationSnapshot,
    DecisionStage,
    MasterPromptRecord,
    MentionSnippet,
    OrganicRankSnapshot,
    PromptType,
    RankQueryKind,
    SearchIntent,
    Verdict,
)
from tests.modules.control_plane.conftest import NOW

GEP_URL = "https://www.gep.com/software/gep-smart"
COUPA_URL = "https://www.coupa.com/p2p"
G2_URL = "https://www.g2.com/categories/procurement"


def _record(pid: str, lob: str, mapped: str | None) -> MasterPromptRecord:
    return MasterPromptRecord(
        prompt_id=pid,
        lob=lob,
        subtopic="Procurement",
        core_keyword="procurement software",
        search_volume=500,
        prompt_text="p",
        search_intent=SearchIntent.COMMERCIAL,
        decision_stage=DecisionStage.CONSIDERATION,
        prompt_type=PromptType.NON_BRANDED,
        web_triggers=True,
        mapped_url=mapped,
        verdict=Verdict.KEEP,
        verdict_reason="x",
    )


def _snapshot(engine: Engine, at, cited: int, mention: float) -> CitationSnapshot:
    return CitationSnapshot(
        engine=engine,
        model="m",
        captured_at=at,
        samples=2,
        web_trigger_rate=1.0,
        client_cited_samples=cited,
        client_citation_rate=cited / 2,
        client_cited=cited >= 1,
        client_best_rank=1 if cited else None,
        client_mean_rank=1.0 if cited else None,
        cited_domains=["coupa.com", "g2.com"] + (["gep.com"] if cited else []),
        competitor_citations={"coupa.com": 1},
        mention_rate=mention,
    )


def _sample(pid: str, engine: Engine, at, cited: bool, *, mention: bool) -> AnswerSample:
    links = [
        Citation(url=COUPA_URL, domain="coupa.com", title="Coupa P2P", position=1),
        Citation(url=G2_URL, domain="g2.com", title="G2 category", position=2),
    ]
    if cited:
        links.append(Citation(url=GEP_URL, domain="gep.com", title="GEP SMART", position=3))
    text = (
        "Coupa leads mid-market P2P [1]. Many buyers compare on G2 [2]. "
        + ("GEP SMART is recommended for source-to-pay [3]. " if mention else "")
        + "Choose based on ERP fit."
    )
    return AnswerSample(
        prompt_id=pid,
        engine=engine,
        model="m",
        captured_at=at,
        web_triggered=True,
        client_cited=cited,
        client_rank=3 if cited else None,
        cited_domains=[c.domain for c in links],
        citation_links=links,
        consulted_urls=[] if cited else ["https://www.gep.com/rejected-page"],
        mention_detected=mention,
        mentions=[
            MentionSnippet(
                entity="client",
                term="GEP",
                snippet="GEP SMART is recommended for source-to-pay [3].",
            )
        ]
        if mention
        else [],
        answer_text=text,
        search_queries=["procurement software comparison", "gep smart vs coupa"],
        citation_claims=[
            CitationClaim(
                url=COUPA_URL, sentence="Coupa leads mid-market P2P [1].", start=0, end=31
            ),
            CitationClaim(url=G2_URL, sentence="Many buyers compare on G2 [2].", start=32, end=62),
        ],
        source_snippets=[
            SourceSnippet(
                url=COUPA_URL, snippet="Coupa P2P overview", title="Coupa P2P", date="2026-06-01"
            ),
            SourceSnippet(url=GEP_URL, snippet="GEP SMART", title="GEP", date="2023-01-01"),
        ],
    )


def _seed_crawl(db, positions, project, prompts, crawl: int) -> None:
    """One full crawl: crawl 0 poor (mentioned, not cited), crawl 1 cited on Perplexity."""
    at = NOW + timedelta(days=2 * crawl)
    run = f"run-{crawl}"
    for prompt in prompts:
        for engine in (Engine.PERPLEXITY, Engine.CHATGPT_SEARCH, Engine.GOOGLE_AI_OVERVIEW):
            cited = 2 if (crawl == 1 and engine is Engine.PERPLEXITY) else 0
            db.record_snapshot(prompt.prompt_id, _snapshot(engine, at, cited, mention=1.0), run)
            db.record_samples(
                prompt.prompt_id,
                [_sample(prompt.prompt_id, engine, at, cited > 0, mention=True) for _ in range(2)],
                run,
            )
        db.record_organic(
            prompt.prompt_id,
            OrganicRankSnapshot(
                query=prompt.prompt_text,
                query_kind=RankQueryKind.PROMPT,
                captured_at=at,
                samples=1,
                client_position=2,
                paa_questions=["What is P2P?"],
                related_searches=["procurement software list"],
            ),
            run,
        )
    positions.record_project_run(
        ProjectRunRecord(
            id=f"crawl-{crawl}-abcdef",
            project_id=project.id,
            started_at=at,
            finished_at=at,
            run_ids=[run],
            prompts_run=2,
            batches=1,
            statuses=["success"],
            full=True,
        )
    )


@pytest.fixture
def seeded(store, db, project, prompts):
    """Prompt records plus crawl 0; tests add crawl 1 when they need movement."""
    store.update_prompt(project.id, prompts[0].id, TrackedPromptUpdate(subtopic="Procurement"))
    store.update_prompt(project.id, prompts[1].id, TrackedPromptUpdate(subtopic="Procurement"))
    positions = PositionStore(db.path)
    for i, prompt in enumerate(prompts):
        db.upsert_prompt(
            _record(prompt.prompt_id, project.client.lob, GEP_URL if i else None), "GEP"
        )
    _seed_crawl(db, positions, project, prompts, 0)
    return positions


def _engine(db, positions) -> InsightEngine:
    return InsightEngine(db, positions, ActionStateStore(db.path))


def test_classify_domain_buckets():
    assert (
        classify_domain("https://www.gep.com/x", "gep.com", ["gep.com"], ["coupa.com"]) == "client"
    )
    assert (
        classify_domain("https://coupa.com", "coupa.com", ["gep.com"], ["coupa.com"])
        == "competitor"
    )
    assert classify_domain("https://www.g2.com/c", "g2.com", [], []) == "aggregator"
    assert classify_domain("https://www.reddit.com/r/x", "reddit.com", [], []) == "forum"
    assert (
        classify_domain("https://marketplace.microsoft.com/p", "microsoft.com", [], [])
        == "marketplace"
    )
    assert (
        classify_domain("https://en.wikipedia.org/wiki/P", "wikipedia.org", [], []) == "reference"
    )
    assert classify_domain("https://docs.acme.com/api", "acme.com", [], []) == "docs"
    assert classify_domain("https://news.example.com/a", "example.com", [], []) == "publisher"


def test_insights_from_latest_crawl_when_no_consolidation(store, db, project, prompts, seeded):
    _seed_crawl(db, seeded, project, prompts, 1)
    view = _engine(db, seeded).build(project, prompts, now=NOW + timedelta(days=3))
    assert view.basis.computed_from == "latest_crawl" and view.basis.low_confidence
    assert view.basis.crawls == 1 and view.basis.consolidation_id is None
    health = {h.engine: h for h in view.health}
    assert (
        health[Engine.PERPLEXITY].verdict == "winning"
        and health[Engine.PERPLEXITY].cited_rate == 1.0
    )
    assert health[Engine.CHATGPT_SEARCH].verdict == "losing"
    assert health[Engine.CHATGPT_SEARCH].losing_to == "coupa.com"
    assert health[Engine.GEMINI].verdict == "invisible" and health[Engine.GEMINI].samples == 0
    assert view.changes == []  # nothing to compare against yet


def test_insights_after_consolidations_have_changes_actions_and_maps(
    store, db, project, prompts, seeded
):
    first = seeded.consolidate(
        project, prompts, window_runs=1, trigger="manual", now=NOW + timedelta(days=1)
    )
    assert first.positions_count == 6
    _seed_crawl(db, seeded, project, prompts, 1)
    seeded_second = seeded.consolidate(
        project, prompts, window_runs=1, trigger="auto", now=NOW + timedelta(days=3)
    )
    engine = _engine(db, seeded)
    view = engine.build(project, prompts, now=NOW + timedelta(days=3))
    assert (
        view.basis.consolidation_id == seeded_second.id
        and view.basis.computed_from == "consolidation"
    )

    kinds = {c.kind for c in view.changes}
    assert "flip_up" in kinds
    assert any(c.engine is Engine.PERPLEXITY and c.after == "100%" for c in view.changes)

    types = {a.type for a in view.actions}
    assert {
        "convert_mention",
        "own_claim",
        "earned_placement",
        "aio_gap",
        "landing_page",
        "read_but_rejected",
        "freshness",
    } <= types
    convert = next(
        a for a in view.actions if a.type == "convert_mention" and a.engine is Engine.CHATGPT_SEARCH
    )
    assert convert.evidence.quotes and "GEP SMART" in convert.evidence.quotes[0].text
    assert (
        convert.evidence.numbers["mention_rate"] == 1.0
        and convert.evidence.numbers["cited_rate"] == 0.0
    )
    assert "gep smart vs coupa" in convert.evidence.queries
    own = next(a for a in view.actions if a.type == "own_claim")
    assert own.evidence.domains[0].domain == "coupa.com" and COUPA_URL in own.evidence.urls
    assert own.evidence.quotes[0].text.startswith("Coupa leads")
    landing = next(a for a in view.actions if a.type == "landing_page")
    assert landing.engine is None and "What is P2P?" in landing.evidence.queries
    rejected = next(a for a in view.actions if a.type == "read_but_rejected")
    assert rejected.evidence.urls == ["https://www.gep.com/rejected-page"]
    assert view.actions == sorted(view.actions, key=lambda a: -a.impact_score)
    assert all(a.status == "open" and a.outcome == "pending" for a in view.actions)

    fan = {f.query: f for f in view.fanout}
    assert (
        fan["gep smart vs coupa"].prompts == 2 and fan["gep smart vs coupa"].client_covered is True
    )
    assert set(fan["gep smart vs coupa"].engines) == {
        Engine.PERPLEXITY,
        Engine.CHATGPT_SEARCH,
        Engine.GOOGLE_AI_OVERVIEW,
    }
    assert any(c.domain == "coupa.com" and c.is_competitor for c in view.claims)
    assert all(not c.is_client for c in view.claims)
    trust = {(t.engine, t.domain_class): t.share for t in view.trust_profile}
    assert (
        trust[(Engine.CHATGPT_SEARCH, "competitor")] == 0.5
        and trust[(Engine.CHATGPT_SEARCH, "aggregator")] == 0.5
    )
    assert trust[(Engine.PERPLEXITY, "client")] == pytest.approx(1 / 3, abs=0.01)
    assert view.read_but_rejected[0].url == "https://www.gep.com/rejected-page"
    assert view.read_but_rejected[0].queries == [
        "gep smart vs coupa",
        "procurement software comparison",
    ]
    assert view.winning_pages[0].url in (COUPA_URL, G2_URL) and view.winning_pages[0].date in (
        "2026-06-01",
        None,
    )
    assert view.client_pages[0].url == GEP_URL and view.client_pages[0].is_client
    placement = {p.engine: p for p in view.placement}
    assert placement[Engine.PERPLEXITY].samples_with_mention == 4
    assert placement[Engine.PERPLEXITY].recommendation_sentence == 4
    fresh = {f.engine: f for f in view.freshness}
    assert fresh[Engine.PERPLEXITY].client_median_age_days is not None
    assert (
        fresh[Engine.PERPLEXITY].client_median_age_days
        > fresh[Engine.PERPLEXITY].competitor_median_age_days
    )


def test_action_state_and_outcome_after_next_consolidation(store, db, project, prompts, seeded):
    engine = _engine(db, seeded)
    seeded.consolidate(
        project, prompts, window_runs=1, trigger="manual", now=NOW + timedelta(days=1)
    )
    view = engine.build(project, prompts, now=NOW + timedelta(days=3))
    card = next(
        a for a in view.actions if a.type == "convert_mention" and a.engine is Engine.CHATGPT_SEARCH
    )

    updated = engine.update_action(
        project,
        prompts,
        card.id,
        ActionUpdate(status="done", owner="Ana", note="fixed H1"),
        now=NOW + timedelta(days=3),
    )
    assert updated.status == "done" and updated.owner == "Ana" and updated.note == "fixed H1"
    again = engine.build(project, prompts, now=NOW + timedelta(days=3))
    same = next(a for a in again.actions if a.id == card.id)
    assert same.status == "done" and same.outcome == "pending"  # same basis: not scored yet

    _seed_crawl(db, seeded, project, prompts, 1)
    seeded.consolidate(project, prompts, window_runs=1, trigger="auto", now=NOW + timedelta(days=5))
    later = engine.build(project, prompts, now=NOW + timedelta(days=5))
    scored = next(a for a in later.actions if a.id == card.id)
    assert scored.outcome == "unchanged"  # cited rate on ChatGPT did not move
    reopened = engine.update_action(
        project, prompts, card.id, ActionUpdate(status="open"), now=NOW + timedelta(days=5)
    )
    assert reopened.status == "open"
    with pytest.raises(KeyError):
        engine.update_action(project, prompts, "nope", ActionUpdate(status="done"))


def test_insights_with_nothing_recorded(store, db, project, prompts):
    view = _engine(db, PositionStore(db.path)).build(project, prompts, now=NOW)
    assert view.basis.computed_from == "none" and view.basis.crawls == 0
    assert all(h.verdict == "invisible" for h in view.health)
    assert view.actions == [] and view.fanout == [] and view.claims == []
