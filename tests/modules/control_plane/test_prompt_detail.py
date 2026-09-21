"""Per-prompt detail: the four engine states, capture coverage, and history that
survives a text edit.

The edge cases exercised here are the ones a prompt-centric view cannot fake its
way through: an engine that was asked and failed reads differently from one never
asked, a minority citation is not an absence, and editing a prompt's wording must
not detach the series behind it.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from src.integrations.schemas import Engine
from src.modules.control_plane.positioning import PositionStore
from src.modules.control_plane.runner import ProjectRunner
from src.modules.control_plane.schemas import (
    ConsolidateRequest,
    EngineStatus,
    ProjectCreate,
    ProjectRunRecord,
    ProjectUpdate,
    TrackedPromptUpdate,
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


def _master(prompt_id: str, text: str, *, mapped_url: str | None = None) -> MasterPromptRecord:
    return MasterPromptRecord(
        prompt_id=prompt_id,
        lob="Procurement Software",
        subtopic="Suites",
        core_keyword="procurement software",
        search_volume=100,
        prompt_text=text,
        search_intent=SearchIntent.COMMERCIAL,
        decision_stage=DecisionStage.CONSIDERATION,
        prompt_type=PromptType.NON_BRANDED,
        web_triggers=True,
        verdict=Verdict.KEEP,
        verdict_reason="tracked",
        mapped_url=mapped_url,
    )


def _snapshot(engine: Engine, **over: object) -> CitationSnapshot:
    base: dict[str, object] = {
        "engine": engine,
        "model": "m1",
        "captured_at": NOW,
        "samples": 3,
        "failed_samples": 0,
        "web_trigger_rate": 1.0,
        "client_cited_samples": 3,
        "client_citation_rate": 1.0,
        "client_cited": True,
        "client_best_rank": 1,
    }
    return CitationSnapshot.model_validate({**base, **over})


@pytest.fixture
def positions(db):
    return PositionStore(db.path)


@pytest.fixture
def runner(store, db, settings, positions):
    return ProjectRunner(
        store,
        db,
        settings=settings,
        pipeline=lambda *_: None,
        clock=lambda: NOW,
        positions=positions,
    )


@pytest.fixture
def seeded(store, db, project, prompts):
    """Prompt 0 has ChatGPT data, a failed Gemini, an untouched Perplexity."""
    tracked = prompts[0]
    pid = tracked.prompt_id
    db.upsert_prompt(_master(pid, tracked.prompt_text, mapped_url="https://gep.com/smart"), "GEP")
    db.record_snapshot(pid, _snapshot(Engine.CHATGPT_SEARCH), "run-1")
    db.record_snapshot(
        pid,
        _snapshot(
            Engine.GEMINI,
            samples=2,
            failed_samples=2,
            client_cited_samples=0,
            client_citation_rate=0.0,
            client_cited=False,
            client_best_rank=None,
        ),
        "run-1",
    )
    db.record_samples(
        pid,
        [
            AnswerSample(
                prompt_id=pid,
                engine=Engine.CHATGPT_SEARCH,
                model="m1",
                captured_at=NOW,
                web_triggered=True,
                client_cited=True,
                answer_text="GEP SMART covers source to pay.",
                search_queries=["gep smart source to pay"],
            ),
            AnswerSample(  # pre-0011: no rich capture
                prompt_id=pid,
                engine=Engine.CHATGPT_SEARCH,
                model="m1",
                captured_at=NOW - timedelta(days=1),
                web_triggered=True,
                client_cited=False,
            ),
        ],
        "run-1",
    )
    db.record_organic(
        pid,
        OrganicRankSnapshot(
            query=tracked.prompt_text,
            query_kind=RankQueryKind.PROMPT,
            samples=1,
            client_position=4,
            paa_questions=["what is source to pay?"],
        ),
        "run-1",
    )
    return tracked


def test_detail_assembles_every_layer_without_rebuilding_insights(runner, project, seeded):
    detail = runner.prompt_detail(project.id, seeded.id)
    assert detail.project_id == project.id
    assert detail.lob == "Procurement Software"
    assert detail.result.prompt.prompt_id == seeded.prompt_id
    assert detail.run_ids == ["run-1"]
    assert detail.content_gap is False  # a landing page is mapped
    assert [o.query_kind for o in detail.organic_prompt] == [RankQueryKind.PROMPT]
    assert detail.organic_prompt[0].paa_questions == ["what is source to pay?"]
    assert detail.organic_keyword == []
    assert detail.shared_lob_projects == []


def test_engine_status_tells_the_four_empty_cells_apart(runner, store, project, seeded):
    """Never asked, asked-and-failed, and not tracked must not all read as absent."""
    store.update_project(
        project.id,
        ProjectUpdate(engines=[Engine.CHATGPT_SEARCH, Engine.GEMINI, Engine.PERPLEXITY]),
    )
    by_engine = {e.engine: e for e in runner.prompt_detail(project.id, seeded.id).engines}

    assert by_engine[Engine.CHATGPT_SEARCH].status is EngineStatus.HAS_DATA
    assert by_engine[Engine.CHATGPT_SEARCH].ok_samples == 3

    gemini = by_engine[Engine.GEMINI]
    assert gemini.status is EngineStatus.ASKED_FAILED
    assert (gemini.samples, gemini.failed_samples, gemini.ok_samples) == (2, 2, 0)

    assert by_engine[Engine.PERPLEXITY].status is EngineStatus.NEVER_ASKED
    assert by_engine[Engine.PERPLEXITY].crawls == 0
    # dropped from the project, but the crawls it already paid for are still shown
    assert Engine.GOOGLE_AI_OVERVIEW not in by_engine


def test_dropped_platform_keeps_its_history(runner, store, db, project, seeded):
    db.record_snapshot(seeded.prompt_id, _snapshot(Engine.GOOGLE_AI_OVERVIEW), "run-1")
    store.update_project(project.id, ProjectUpdate(engines=[Engine.CHATGPT_SEARCH]))
    by_engine = {e.engine: e for e in runner.prompt_detail(project.id, seeded.id).engines}
    aio = by_engine[Engine.GOOGLE_AI_OVERVIEW]
    assert aio.status is EngineStatus.NOT_CONFIGURED
    assert aio.crawls == 1 and aio.cited is True


def test_minority_citation_is_not_an_absence(runner, db, project, seeded):
    """`client_cited` is a majority verdict; a real rank below it must still surface."""
    db.record_snapshot(
        seeded.prompt_id,
        _snapshot(
            Engine.CHATGPT_SEARCH,
            captured_at=NOW + timedelta(days=1),
            samples=3,
            client_cited_samples=1,
            client_citation_rate=0.3333,
            client_cited=False,
            client_best_rank=2,
        ),
        "run-2",
    )
    chatgpt = next(
        e
        for e in runner.prompt_detail(project.id, seeded.id).engines
        if e.engine is Engine.CHATGPT_SEARCH
    )
    assert chatgpt.cited is False
    assert chatgpt.cited_in_minority is True
    assert chatgpt.best_rank == 2
    assert chatgpt.citation_rate == 0.3333  # stored, not recomputed


def test_capture_coverage_separates_no_answer_from_no_citation(runner, project, seeded):
    capture = runner.prompt_detail(project.id, seeded.id).capture
    assert capture.samples == 2
    assert capture.with_answer_text == 1  # the pre-0011 sample has none
    assert capture.with_search_queries == 1
    assert capture.with_citation_claims == 0
    assert capture.first_captured_at is not None


def test_editing_the_text_keeps_the_prompt_and_its_history(runner, store, project, seeded):
    """The whole point of the identity freeze: a typo fix must not zero the series."""
    before = runner.prompt_detail(project.id, seeded.id)
    updated = store.update_prompt(
        project.id, seeded.id, TrackedPromptUpdate(prompt_text="What is the best P2P suite?")
    )
    after = runner.prompt_detail(project.id, seeded.id)

    assert updated.prompt_text == "What is the best P2P suite?"
    assert after.result.prompt.prompt_id == before.result.prompt.prompt_id
    assert after.run_ids == before.run_ids
    assert after.capture.samples == before.capture.samples
    chatgpt = next(e for e in after.engines if e.engine is Engine.CHATGPT_SEARCH)
    assert chatgpt.status is EngineStatus.HAS_DATA and chatgpt.crawls == 1


def test_renaming_the_lob_keeps_every_prompt_history(runner, store, project, seeded):
    store.update_project(
        project.id,
        ProjectUpdate(client={**project.client.model_dump(), "lob": "Sourcing"}),
    )
    detail = runner.prompt_detail(project.id, seeded.id)
    assert detail.lob == "Sourcing"
    assert detail.run_ids == ["run-1"]


def test_samples_are_scoped_to_the_project(runner, store, project, seeded):
    """History is keyed by (lob, text) alone, so the route must check ownership."""
    other = store.create_project(ProjectCreate(name="Other", client=project.client))
    with pytest.raises(KeyError):
        runner.samples(other.id, seeded.prompt_id, None, None)
    assert runner.samples(project.id, seeded.prompt_id, None, None)


def test_samples_limit_is_clamped(runner, project, seeded):
    assert runner.samples(project.id, seeded.prompt_id, None, None, limit=1) != []
    assert len(runner.samples(project.id, seeded.prompt_id, None, None, limit=1)) == 1
    # a hostile limit cannot ask the database for everything
    assert len(runner.samples(project.id, seeded.prompt_id, None, None, limit=10_000)) == 2


def test_positions_span_consolidations(runner, positions, project, seeded):
    positions.record_project_run(
        ProjectRunRecord(
            id="crawl-001-abc",
            project_id=project.id,
            started_at=NOW,
            finished_at=NOW,
            run_ids=["run-1"],
            prompts_run=2,
            batches=1,
            statuses=["success"],
            full=True,
        )
    )
    runner.consolidate(project.id, ConsolidateRequest(note="first"))
    detail = runner.prompt_detail(project.id, seeded.id)
    assert len(detail.positions) >= 1
    first = detail.positions[0]
    assert first.window_runs >= 1
    assert first.position.prompt_id == seeded.prompt_id


def test_never_sampled_prompt_reports_no_data_rather_than_zero(runner, project, prompts):
    detail = runner.prompt_detail(project.id, prompts[1].id)
    assert detail.run_ids == []
    assert detail.capture.samples == 0
    assert detail.content_gap is True  # no prompts row at all
    assert {e.status for e in detail.engines} == {EngineStatus.NEVER_ASKED}
    assert all(e.citation_rate is None for e in detail.engines)


def test_shared_lob_projects_are_named(runner, store, project, seeded):
    store.create_project(ProjectCreate(name="Second GEP", client=project.client))  # same lob
    detail = runner.prompt_detail(project.id, seeded.id)
    assert detail.shared_lob_projects == ["Second GEP"]
