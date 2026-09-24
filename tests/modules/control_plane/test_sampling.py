"""The sampling policy: stretch, boost, budget, and how the runner applies it (ADR 0025)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from src.integrations.schemas import Engine
from src.modules.control_plane.app import create_app
from src.modules.control_plane.jobs import JobManager
from src.modules.control_plane.planner import batches
from src.modules.control_plane.positioning import PositionStore
from src.modules.control_plane.runner import ProjectRunner
from src.modules.control_plane.sampling import plan
from src.modules.control_plane.schemas import (
    ProjectRunRecord,
    ProjectUpdate,
    RunRequest,
    SamplingSummary,
    TrackedPromptCreate,
    TrackedPromptUpdate,
)
from src.modules.prompt_tracking.schemas import (
    CitationSnapshot,
    DecisionStage,
    MasterPromptRecord,
    PromptType,
    SearchIntent,
    StabilityState,
    Verdict,
)
from tests.modules.control_plane.conftest import NOW
from tests.modules.control_plane.test_runner import FakePipeline

DAY = timedelta(days=1)
CHAT = Engine.CHATGPT_SEARCH
PPLX = Engine.PERPLEXITY


def _seed_prompt(db, prompt) -> None:
    db.upsert_prompt(
        MasterPromptRecord(
            prompt_id=prompt.prompt_id,
            lob="Procurement Software",
            subtopic="S",
            core_keyword="k",
            search_volume=0,
            prompt_text=prompt.prompt_text,
            search_intent=SearchIntent.INFORMATIONAL,
            decision_stage=DecisionStage.AWARENESS,
            prompt_type=PromptType.NON_BRANDED,
            web_triggers=True,
            verdict=Verdict.KEEP,
            verdict_reason="x",
        ),
        "GEP",
    )


def seed(db, prompt, engine, crawls: list[tuple[int, int]], *, samples: int = 3) -> None:
    """`crawls` are (days ago, cited samples) pairs; all on one model."""
    _seed_prompt(db, prompt)
    for days_ago, cited in crawls:
        db.record_snapshot(
            prompt.prompt_id,
            CitationSnapshot(
                engine=engine,
                model="m",
                captured_at=NOW - days_ago * DAY,
                samples=samples,
                web_trigger_rate=1.0,
                client_cited_samples=cited,
                client_citation_rate=cited / samples,
                client_cited=cited * 2 >= samples,
            ),
            f"run-{days_ago}",
        )


STABLE = [(1, 0), (2, 0), (3, 0), (4, 0)]  # 0 of 12: settled, newest one day ago
VOLATILE = [(1, 2), (2, 1), (3, 2), (4, 1)]  # 6 of 12: a coin flip


@pytest.fixture
def positions(db) -> PositionStore:
    return PositionStore(db.path)


@pytest.fixture
def tuned(settings):
    return settings.model_copy(
        update={
            "samples_per_engine": 3,
            "min_samples": 2,
            "adaptive_sampling": True,
            "stability_window_crawls": 4,
            "stability_min_crawls": 3,
            "stretch_max": 3,
            "volatile_boost": 2,
        }
    )


@pytest.fixture
def one_engine(store, project):
    """Daily project on ChatGPT and Perplexity only, window of three crawls."""
    return store.update_project(
        project.id, ProjectUpdate(engines=[CHAT, PPLX], consolidation_runs=3)
    )


def _decision(result, prompt, engine):
    return next(d for d in result.decisions if d.tracked_id == prompt.id and d.engine is engine)


# -- fixed --------------------------------------------------------------------------


def test_fixed_policy_changes_nothing_and_does_not_judge(one_engine, prompts, db, positions, tuned):
    plain = prompts[0]
    seed(db, plain, CHAT, STABLE)
    result = plan(one_engine, [plain], db, positions, tuned, NOW)
    d = _decision(result, plain, CHAT)
    assert d.stability.state is StabilityState.UNKNOWN and "fixed" in d.stability.reason
    assert d.due and d.multiplier == 1 and not d.skipped
    assert [i.engines for i in result.items] == [[CHAT, PPLX]]
    s = result.summary
    assert s.policy == "fixed" and s.calls_planned == s.calls_baseline == 6
    assert s.skipped_pairs == 0 and s.boosted_pairs == 0


# -- save ---------------------------------------------------------------------------


def test_save_stretches_a_stable_pair_and_reports_the_saving(
    store, one_engine, prompts, db, positions, tuned
):
    project = store.update_project(one_engine.id, ProjectUpdate(sampling_policy="save"))
    plain = prompts[0]
    seed(db, plain, CHAT, STABLE)
    seed(db, plain, PPLX, VOLATILE)
    result = plan(project, [plain], db, positions, tuned, NOW)
    chat = _decision(result, plain, CHAT)
    assert chat.stability.state is StabilityState.STABLE
    assert chat.multiplier == 2 and chat.stretched and chat.skipped and not chat.due
    assert chat.next_due_at == NOW + DAY  # newest 1 day ago, every 2 days now
    assert "every 2 intervals" in chat.note
    pplx = _decision(result, plain, PPLX)
    assert pplx.stability.state is StabilityState.VOLATILE and pplx.due and not pplx.boosted
    assert [i.engines for i in result.items] == [[PPLX]]
    s = result.summary
    assert s.stretched_pairs == 1 and s.skipped_pairs == 1 and s.calls_saved == 2
    assert s.calls_baseline == 2 + 3 and s.calls_planned == 3  # stable pair early-stops at 2
    assert s.next_due_at == NOW + DAY
    assert s.est_cost_baseline_usd == pytest.approx(2 * 0.03 + 3 * 0.02)
    assert s.est_cost_planned_usd == pytest.approx(3 * 0.02)


def test_stretch_tiers_and_caps(store, one_engine, prompts, db, positions, tuned):
    plain = prompts[0]
    long_stable = [(d, 0) for d in range(1, 10)]  # streak 9 >= 2 x window
    seed(db, plain, CHAT, long_stable)
    project = store.update_project(one_engine.id, ProjectUpdate(sampling_policy="save"))
    assert _decision(plan(project, [plain], db, positions, tuned, NOW), plain, CHAT).multiplier == 3
    # capped by the consolidation window so the pair appears in every consolidation
    narrow = store.update_project(project.id, ProjectUpdate(consolidation_runs=2))
    assert _decision(plan(narrow, [plain], db, positions, tuned, NOW), plain, CHAT).multiplier == 2
    single = store.update_project(project.id, ProjectUpdate(consolidation_runs=1))
    d = _decision(plan(single, [plain], db, positions, tuned, NOW), plain, CHAT)
    assert d.multiplier == 1 and "consolidation window is one crawl" in d.note
    capped = tuned.model_copy(update={"stretch_max": 2})
    assert (
        _decision(plan(project, [plain], db, positions, capped, NOW), plain, CHAT).multiplier == 2
    )


def test_starred_overridden_forced_and_selected_runs_are_never_stretched(
    store, one_engine, prompts, db, positions, tuned
):
    project = store.update_project(one_engine.id, ProjectUpdate(sampling_policy="save"))
    plain, starred = prompts
    seed(db, plain, CHAT, STABLE)
    seed(db, starred, CHAT, STABLE)
    result = plan(project, [plain, starred], db, positions, tuned, NOW)
    assert _decision(result, plain, CHAT).stretched
    star = _decision(result, starred, CHAT)
    assert not star.stretched and "Starred" in star.note
    # starred has interval 2d: newest 1 day ago -> not base-due either
    assert not star.due and not star.skipped

    own = store.update_prompt(project.id, plain.id, TrackedPromptUpdate(interval="daily"))
    d = _decision(plan(project, [own], db, positions, tuned, NOW), own, CHAT)
    assert not d.stretched and "own interval" in d.note and d.due

    forced = plan(project, [plain], db, positions, tuned, NOW, request=RunRequest(force=True))
    assert _decision(forced, plain, CHAT).due and forced.summary.skipped_pairs == 0
    picked = plan(
        project, [plain], db, positions, tuned, NOW, request=RunRequest(prompt_ids=[plain.id])
    )
    assert _decision(picked, plain, CHAT).due and picked.summary.stretched_pairs == 0


# -- reallocate ---------------------------------------------------------------------


def test_reallocate_boosts_a_volatile_pair_only_from_saved_calls(
    store, one_engine, prompts, db, positions, tuned
):
    project = store.update_project(one_engine.id, ProjectUpdate(sampling_policy="reallocate"))
    plain = prompts[0]
    seed(db, plain, PPLX, VOLATILE)
    # No stable pair, no carry: the boost waits.
    result = plan(project, [plain], db, positions, tuned, NOW)
    pplx = _decision(result, plain, PPLX)
    assert not pplx.boosted and pplx.samples == 3 and "deferred until" in pplx.note
    assert result.summary.calls_boosted == 0

    # A stretched stable pair on the other platform pays for it.
    seed(db, plain, CHAT, STABLE)
    result = plan(project, [plain], db, positions, tuned, NOW)
    pplx = _decision(result, plain, PPLX)
    assert pplx.boosted and pplx.samples == 5 and pplx.expected_calls == 5
    assert result.items[0].boosted == {PPLX.value: 5}
    s = result.summary
    assert s.calls_saved == 2 and s.calls_boosted == 2 and s.boosted_pairs == 1
    assert s.calls_planned == 5 and s.calls_baseline == 5  # 2 (stable) + 3 (volatile)
    assert s.est_cost_planned_usd == pytest.approx(5 * 0.02)

    # The boosted platform becomes its own batch with the higher count.
    groups = batches(project, result.items)
    assert [(g.engines, g.samples_per_engine) for g in groups] == [([PPLX], 5)]


def test_boost_budget_carries_over_from_earlier_crawls_of_the_cycle(
    store, one_engine, prompts, db, positions, tuned
):
    project = store.update_project(one_engine.id, ProjectUpdate(sampling_policy="reallocate"))
    plain = prompts[0]
    seed(db, plain, PPLX, VOLATILE)
    earlier = SamplingSummary(
        policy="reallocate",
        pairs=2,
        due_pairs=1,
        stretched_pairs=1,
        skipped_pairs=1,
        boosted_pairs=0,
        calls_planned=3,
        calls_baseline=5,
        calls_saved=2,
        calls_boosted=0,
        est_cost_planned_usd=0.06,
        est_cost_baseline_usd=0.12,
    )
    positions.record_project_run(
        ProjectRunRecord(
            id="crawl-000001",
            project_id=project.id,
            started_at=NOW - DAY,
            finished_at=NOW - DAY,
            run_ids=["r"],
            prompts_run=1,
            batches=1,
            statuses=["success"],
            full=True,
            sampling=earlier,
        )
    )
    assert positions.project_runs(project.id)[0].sampling == earlier  # round-trips
    result = plan(project, [plain], db, positions, tuned, NOW)
    assert result.summary.carry_calls == 2 and _decision(result, plain, PPLX).boosted
    # A fixed-policy project never reads the carry.
    fixed = store.update_project(project.id, ProjectUpdate(sampling_policy="fixed"))
    assert plan(fixed, [plain], db, positions, tuned, NOW).summary.carry_calls == 0


def test_boost_exclusions(store, one_engine, prompts, db, positions, tuned):
    project = store.update_project(one_engine.id, ProjectUpdate(sampling_policy="reallocate"))
    plain = prompts[0]
    seed(db, plain, CHAT, STABLE)
    # Returned from a stretch: newest two snapshots three days apart on a daily interval.
    seed(db, plain, PPLX, [(1, 2), (4, 1), (5, 2), (6, 1)])
    d = _decision(plan(project, [plain], db, positions, tuned, NOW), plain, PPLX)
    assert not d.boosted and "boost deferred one window" in d.note

    # An explicit sample override is respected.
    own = store.update_prompt(project.id, plain.id, TrackedPromptUpdate(samples_per_engine=4))
    seed(db, own, PPLX, VOLATILE)
    d = _decision(plan(project, [own], db, positions, tuned, NOW), own, PPLX)
    assert not d.boosted and d.samples == 4 and d.base_samples == 4

    # A failing platform is neither stretched nor boosted, and spends nothing extra.
    third = store.add_prompt(project.id, TrackedPromptCreate(prompt_text="third prompt text"))
    _seed_prompt(db, third)
    for days_ago in (1, 2, 3):
        db.record_snapshot(
            third.prompt_id,
            CitationSnapshot(
                engine=PPLX,
                model="m",
                captured_at=NOW - days_ago * DAY,
                samples=3,
                failed_samples=3,
                web_trigger_rate=0.0,
                client_cited_samples=0,
                client_citation_rate=0.0,
                client_cited=False,
            ),
            f"f-{days_ago}",
        )
    d = _decision(plan(project, [plain, third], db, positions, tuned, NOW), third, PPLX)
    assert d.stability.state is StabilityState.FAILING and not d.boosted and d.multiplier == 1

    # Volatile pairs already at the maximum cannot be boosted.
    maxed = store.update_project(project.id, ProjectUpdate(samples_per_engine=10))
    seed(db, plain, PPLX, VOLATILE)
    d = _decision(plan(maxed, [plain], db, positions, tuned, NOW), plain, PPLX)
    assert not d.boosted and "maximum sample count" in d.note


# -- runner and routes ---------------------------------------------------------------


@pytest.fixture
def runner(store, db, positions, tuned):
    fake = FakePipeline(db=db)
    return ProjectRunner(
        store, db, settings=tuned, pipeline=fake, clock=lambda: NOW, positions=positions
    ), fake


def test_runner_applies_the_plan_records_it_and_explains_an_idle_crawl(
    store, one_engine, prompts, db, positions, runner
):
    project = store.update_project(one_engine.id, ProjectUpdate(sampling_policy="save"))
    plain, starred = prompts
    store.update_prompt(project.id, starred.id, TrackedPromptUpdate(enabled=False))
    seed(db, plain, CHAT, STABLE)
    seed(db, plain, PPLX, VOLATILE)
    run, fake = runner

    # Scheduled run: the stable pair is skipped, the volatile one runs, and the crawl
    # record carries the summary.
    outcome = run.run(project.id)
    assert fake.payloads[-1].engines == [PPLX]
    assert outcome.sampling is not None and outcome.sampling.skipped_pairs == 1
    record = positions.project_runs(project.id)[0]
    assert record.full is True and record.sampling == outcome.sampling
    assert record.sampling is not None and record.sampling.calls_saved == 2

    # Nothing else due now: the reason says why, in the analyst's terms.
    idle = run.run(project.id)
    assert idle.batches == 0
    assert idle.reason == "nothing due; 1 stable pair(s) stretched, next due 2026-09-18"
    assert positions.project_runs(project.id)[0].id == record.id  # no crawl recorded
    # Results and the plan agree on what is due.
    assert run.results(project.id)[0].due_on == []

    # A selected run is never adaptive and is not a full crawl.
    picked = run.run(project.id, RunRequest(prompt_ids=[plain.id], engines=[CHAT]))
    assert picked.batches == 1 and fake.payloads[-1].engines == [CHAT]
    assert picked.sampling is not None and picked.sampling.stretched_pairs == 0
    assert positions.project_runs(project.id)[0].full is False


def test_prompt_detail_and_sampling_route(store, one_engine, prompts, db, positions, tuned, runner):
    project = store.update_project(one_engine.id, ProjectUpdate(sampling_policy="fixed"))
    plain = prompts[0]
    seed(db, plain, CHAT, STABLE)
    run, _ = runner
    detail = run.prompt_detail(project.id, plain.id)
    chat = next(e for e in detail.engines if e.engine is CHAT)
    assert chat.stability is not None and chat.stability.state is StabilityState.STABLE
    pplx = next(e for e in detail.engines if e.engine is PPLX)
    assert pplx.stability is not None and pplx.stability.state is StabilityState.UNKNOWN

    jobs = JobManager(run, store, autostart=False, clock=lambda: NOW)
    with TestClient(create_app(store, db, run, jobs, settings=lambda: tuned)) as client:
        view = client.get(f"/api/projects/{project.id}/sampling").json()
        assert view["policy"] == "fixed" and view["simulated"] is False
        assert view["window_crawls"] == 4 and view["stretch_max"] == 3
        assert any("policy is fixed" in w for w in view["warnings"])
        rows = {(r["tracked_id"], r["engine"]): r for r in view["decisions"]}
        assert rows[(plain.id, "CHATGPT_SEARCH")]["stability"]["state"] == "stable"
        assert rows[(plain.id, "CHATGPT_SEARCH")]["multiplier"] == 1

        simulated = client.get(f"/api/projects/{project.id}/sampling?policy=save").json()
        assert simulated["simulated"] is True and simulated["summary"]["skipped_pairs"] == 1
        assert client.get(f"/api/projects/{project.id}/sampling?policy=turbo").status_code == 422
        assert client.get("/api/projects/nope/sampling").status_code == 404

        store.update_project(project.id, ProjectUpdate(reuse_within_hours=24))
        warned = client.get(f"/api/projects/{project.id}/sampling").json()
        assert any("reused for 24 h" in w for w in warned["warnings"])

        # The policy is an ordinary project field.
        res = client.put(f"/api/projects/{project.id}", json={"sampling_policy": "reallocate"})
        assert res.status_code == 200 and res.json()["sampling_policy"] == "reallocate"
        assert (
            client.put(f"/api/projects/{project.id}", json={"sampling_policy": "x"}).status_code
            == 422
        )
