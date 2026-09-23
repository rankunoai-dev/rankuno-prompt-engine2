"""Consolidation window: crawl records, aggregation over N crawls, auto and manual triggers."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.integrations.schemas import Citation, Engine
from src.modules.control_plane.positioning import PositionStore, aggregate_position
from src.modules.control_plane.runner import ProjectRunner
from src.modules.control_plane.schemas import (
    ConsolidateRequest,
    ProjectRunRecord,
    ProjectUpdate,
    RunRequest,
)
from src.modules.prompt_tracking.schemas import (
    AnswerSample,
    CitationSnapshot,
    DecisionStage,
    MasterPromptRecord,
    OrganicRankSnapshot,
    PromptType,
    RankQueryKind,
    SearchIntent,
    Verdict,
)
from tests.modules.control_plane.conftest import NOW
from tests.modules.control_plane.test_runner import FakePipeline


def _seed_prompt(db, pid: str, lob: str) -> None:
    db.upsert_prompt(
        MasterPromptRecord(
            prompt_id=pid,
            lob=lob,
            subtopic="S",
            core_keyword="k",
            search_volume=0,
            prompt_text="p",
            search_intent=SearchIntent.INFORMATIONAL,
            decision_stage=DecisionStage.AWARENESS,
            prompt_type=PromptType.NON_BRANDED,
            web_triggers=True,
            verdict=Verdict.KEEP,
            verdict_reason="x",
        ),
        "GEP",
    )


def _snapshot(
    run: str, at: datetime, cited: int, samples: int = 2, best: int | None = 1, mention: float = 0.5
):
    return CitationSnapshot(
        engine=Engine.PERPLEXITY,
        model="m",
        captured_at=at,
        samples=samples,
        web_trigger_rate=1.0,
        client_cited_samples=cited,
        client_citation_rate=cited / samples,
        client_cited=cited * 2 >= samples,
        client_best_rank=best,
        client_mean_rank=float(best) if best else None,
        cited_domains=["gep.com", "coupa.com"] if cited else ["coupa.com"],
        competitor_citations={"coupa.com": 1 if not cited else 2},
        mention_rate=mention,
    )


def _sample(pid: str, at: datetime, cited: bool, rank: int | None):
    return AnswerSample(
        prompt_id=pid,
        engine=Engine.PERPLEXITY,
        model="m",
        captured_at=at,
        web_triggered=True,
        client_cited=cited,
        client_rank=rank,
        cited_domains=["gep.com", "coupa.com"] if cited else ["coupa.com", "sap.com"],
        citation_links=[Citation(url="https://www.gep.com/x", domain="gep.com", position=1)]
        if cited
        else [],
        mention_detected=cited,
    )


def test_aggregate_position_folds_snapshots_samples_and_organic():
    t = NOW
    snaps = [
        {
            "run_id": "r1",
            "captured_at": t.isoformat(),
            "samples": 2,
            "failed_samples": 0,
            "client_cited_samples": 2,
            "client_best_rank": 1,
            "client_mean_rank": 1.0,
            "cited_domains": '["gep.com","coupa.com"]',
            "competitor_citations": '{"coupa.com": 2}',
            "mention_rate": 1.0,
        },
        {
            "run_id": "r2",
            "captured_at": (t + timedelta(days=2)).isoformat(),
            "samples": 2,
            "failed_samples": 1,
            "client_cited_samples": 0,
            "client_best_rank": None,
            "client_mean_rank": None,
            "cited_domains": '["coupa.com"]',
            "competitor_citations": '{"coupa.com": 1}',
            "mention_rate": 0.0,
        },
        {
            "run_id": "r3",
            "captured_at": (t + timedelta(days=4)).isoformat(),
            "samples": 2,
            "failed_samples": 0,
            "client_cited_samples": 1,
            "client_best_rank": 3,
            "client_mean_rank": 3.0,
            "cited_domains": '["coupa.com","gep.com"]',
            "competitor_citations": '{"coupa.com": 1}',
            "mention_rate": 0.5,
        },
    ]
    samples = [
        {
            "run_id": "r1",
            "client_cited": 1,
            "client_rank": 1,
            "cited_domains": '["gep.com","coupa.com"]',
            "mention_detected": 1,
        },
        {
            "run_id": "r1",
            "client_cited": 1,
            "client_rank": 1,
            "cited_domains": '["gep.com"]',
            "mention_detected": 1,
        },
        {
            "run_id": "r2",
            "client_cited": 0,
            "client_rank": None,
            "cited_domains": '["coupa.com"]',
            "mention_detected": 0,
        },
        {
            "run_id": "r3",
            "client_cited": 1,
            "client_rank": 3,
            "cited_domains": '["coupa.com","gep.com"]',
            "mention_detected": 1,
        },
        {
            "run_id": "r3",
            "client_cited": 0,
            "client_rank": None,
            "cited_domains": '["coupa.com"]',
            "mention_detected": 0,
        },
    ]
    organic = [
        {"query_kind": "PROMPT", "client_position": 4},
        {"query_kind": "PROMPT", "client_position": 2},
        {"query_kind": "KEYWORD", "client_position": None},
    ]
    p = aggregate_position("p1", Engine.PERPLEXITY, snaps, samples, organic)
    assert p is not None
    assert (p.runs, p.samples, p.failed_samples, p.cited_samples) == (3, 6, 1, 3)
    assert p.citation_rate == 0.6 and p.cited is True  # 3 of 5 ok samples
    # 95% band around 3 of 5: wide, and it brackets the point estimate.
    assert p.citation_rate_low is not None and p.citation_rate_high is not None
    assert p.citation_rate_low < 0.6 < p.citation_rate_high
    assert (p.citation_rate_low, p.citation_rate_high) == (0.2307, 0.8824)
    assert p.mention_rate_low is not None and p.mention_rate_high is not None
    assert p.mention_rate_low <= p.mention_rate <= p.mention_rate_high
    # 95% band around 3 of 5: wide, and it brackets the point estimate.
    assert p.citation_rate_low is not None and p.citation_rate_high is not None
    assert p.citation_rate_low < 0.6 < p.citation_rate_high
    assert (p.citation_rate_low, p.citation_rate_high) == (0.2307, 0.8824)
    assert p.mention_rate_low is not None and p.mention_rate_high is not None
    assert p.mention_rate_low <= p.mention_rate <= p.mention_rate_high
    # 95% band around 3 of 5: wide, and it brackets the point estimate.
    assert p.citation_rate_low is not None and p.citation_rate_high is not None
    assert p.citation_rate_low < 0.6 < p.citation_rate_high
    assert (p.citation_rate_low, p.citation_rate_high) == (0.2307, 0.8824)
    assert p.mention_rate_low is not None and p.mention_rate_high is not None
    assert p.mention_rate_low <= p.mention_rate <= p.mention_rate_high
    assert p.mention_samples == 3 and p.mention_rate == 0.6 and p.mentioned is True
    assert p.best_rank == 1 and p.mean_rank == pytest.approx((1.0 * 2 + 3.0 * 1) / 3, abs=0.01)
    assert p.rank_distribution == {"1": 2, "not cited": 2, "3": 1}
    assert p.cited_domain_share == {"coupa.com": 0.8, "gep.com": 0.6}
    assert p.competitor_citations == {"coupa.com": 1}
    assert (p.organic_prompt_best, p.organic_prompt_mean) == (2, 3.0)
    assert p.organic_keyword_best is None
    assert p.first_run_at == t and p.last_run_at == t + timedelta(days=4)


def test_aggregate_position_without_samples_falls_back_to_snapshots():
    snaps = [
        {
            "run_id": "r1",
            "captured_at": NOW.isoformat(),
            "samples": 3,
            "failed_samples": 0,
            "client_cited_samples": 3,
            "client_best_rank": 2,
            "client_mean_rank": 2.0,
            "cited_domains": '["gep.com"]',
            "competitor_citations": "{}",
            "mention_rate": 0.0,
        },
    ]
    p = aggregate_position("p1", Engine.GEMINI, snaps, [], [])
    assert (
        p is not None
        and p.rank_distribution == {"2": 1}
        and p.cited_domain_share == {"gep.com": 1.0}
    )
    assert aggregate_position("p1", Engine.GEMINI, [], [], []) is None


def test_store_records_crawls_and_consolidates_over_the_window(store, db, project, prompts):
    positions = PositionStore(db.path)
    pid = prompts[0].prompt_id
    _seed_prompt(db, pid, project.client.lob)
    for i, cited in enumerate((2, 0, 1, 2)):
        at = NOW + timedelta(days=2 * i)
        run = f"run-{i}"
        db.record_snapshot(pid, _snapshot(run, at, cited), run)
        db.record_samples(
            pid,
            [
                _sample(pid, at, cited > 0, 1 if cited else None),
                _sample(pid, at, cited > 1, 1 if cited > 1 else None),
            ],
            run,
        )
        db.record_organic(
            pid,
            OrganicRankSnapshot(
                query="p",
                query_kind=RankQueryKind.PROMPT,
                captured_at=at,
                samples=1,
                client_position=3 + i,
            ),
            run,
        )
        positions.record_project_run(
            ProjectRunRecord(
                id=f"crawl-00{i}-abc",
                project_id=project.id,
                started_at=at,
                finished_at=at,
                run_ids=[run],
                prompts_run=2,
                batches=1,
                statuses=["success"],
                full=i != 1,
            )
        )
    assert [c.id for c in positions.project_runs(project.id)] == [
        "crawl-003-abc",
        "crawl-002-abc",
        "crawl-001-abc",
        "crawl-000-abc",
    ]
    assert [c.id for c in positions.project_runs(project.id, full_only=True)] == [
        "crawl-003-abc",
        "crawl-002-abc",
        "crawl-000-abc",
    ]
    assert positions.runs_since_last_consolidation(project.id) == 3

    c = positions.consolidate(
        project, prompts, window_runs=3, trigger="manual", note="first", now=NOW + timedelta(days=7)
    )
    assert c.project_run_ids == [
        "crawl-003-abc",
        "crawl-002-abc",
        "crawl-000-abc",
    ]  # crawl-1 was a partial run
    assert c.run_ids == ["run-0", "run-2", "run-3"] and c.positions_count == 1
    assert c.first_run_at == NOW and c.last_run_at == NOW + timedelta(days=6)
    view = positions.positions(project.id)
    assert view.consolidation is not None and view.consolidation.id == c.id
    assert view.runs_since_last == 0 and [h.id for h in view.history] == [c.id]
    (pos,) = view.positions
    assert pos.prompt_id == pid and pos.engine is Engine.PERPLEXITY
    assert pos.runs == 3 and pos.samples == 6 and pos.cited_samples == 5
    assert pos.citation_rate == pytest.approx(5 / 6, abs=1e-4) and pos.cited is True
    assert pos.rank_distribution == {"1": 5, "not cited": 1}  # crawls 0, 2, 3: 2+1+2 cited
    assert pos.organic_prompt_best == 3 and pos.organic_prompt_mean == pytest.approx(
        (3 + 5 + 6) / 3, abs=0.01
    )

    later = positions.consolidate(
        project, prompts, window_runs=1, trigger="manual", now=NOW + timedelta(days=8)
    )
    assert positions.positions(project.id).consolidation.id == later.id  # newest first
    assert positions.positions(project.id, c.id).consolidation.id == c.id
    with pytest.raises(KeyError):
        positions.positions(project.id, "missing")
    empty = project.model_copy(update={"id": "no-runs-here"})
    with pytest.raises(ValueError, match="No completed crawl"):
        positions.consolidate(empty, prompts, window_runs=3, trigger="manual")


def test_runner_records_crawls_and_auto_consolidates_every_n_full_runs(
    store, db, project, prompts, settings
):
    store.update_project(project.id, ProjectUpdate(consolidation_runs=2))
    clock_now = [NOW]
    runner = ProjectRunner(
        store, db, settings=settings, pipeline=FakePipeline(db=db), clock=lambda: clock_now[0]
    )
    first = runner.run(project.id)
    assert first.batches == 1 and first.project_run_id and first.consolidation_id is None
    assert runner.positions(project.id).runs_since_last == 1
    # A selected-prompt run is stored but does not count towards the window.
    clock_now[0] = NOW + timedelta(days=1)
    partial = runner.run(project.id, RunRequest(force=True, prompt_ids=[prompts[0].id]))
    assert partial.project_run_id and partial.consolidation_id is None
    assert runner.positions(project.id).runs_since_last == 1
    clock_now[0] = NOW + timedelta(days=2)
    second = runner.run(project.id, RunRequest(force=True))
    assert second.consolidation_id is not None
    view = runner.positions(project.id)
    assert view.consolidation is not None and view.consolidation.trigger == "auto"
    assert view.consolidation.window_runs == 2 and view.runs_since_last == 0
    assert len(view.positions) == 2 * len(Engine)  # two prompts x four platforms
    assert all(p.runs == 2 and p.samples == 2 for p in view.positions)
    assert [c.full for c in runner.crawls(project.id)] == [True, False, True]
    manual = runner.consolidate(project.id, ConsolidateRequest(window_runs=1, note="check"))
    assert manual.trigger == "manual" and manual.window_runs == 1 and manual.note == "check"
    assert runner.positions(project.id).consolidation.id == manual.id
    with pytest.raises(KeyError):
        runner.positions("nope")
