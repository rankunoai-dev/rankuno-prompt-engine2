"""ProjectRunner: batches to pipeline inputs, generation cadence, progress, results view."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import BaseModel

from src.core.schemas import ExecutionStatus, ToolResult
from src.integrations.schemas import Engine
from src.modules.control_plane.runner import ProjectRunner, engine_options
from src.modules.control_plane.schemas import (
    ProjectUpdate,
    RunProgress,
    RunRequest,
    TrackedPromptUpdate,
)
from src.modules.prompt_tracking.schemas import (
    CitationSnapshot,
    DecisionStage,
    MasterPromptRecord,
    PipelineInput,
    PipelinePhase,
    PipelineProgress,
    PromptType,
    SearchIntent,
    Verdict,
    prompt_id_for,
)
from tests.modules.control_plane.conftest import NOW


class Summary(BaseModel):
    run_id: str
    warnings: list[str] = []


class FakePipeline:
    """Stands in for the tool: records payloads, persists snapshots, emits progress."""

    def __init__(self, status: ExecutionStatus = ExecutionStatus.SUCCESS, db=None) -> None:
        self.payloads: list[PipelineInput] = []
        self.status = status
        self.db = db

    def __call__(self, payload: PipelineInput, progress=None) -> ToolResult[BaseModel]:
        self.payloads.append(payload)
        prompts = len(payload.custom_prompts) or (20 if payload.generate_prompts else 0)
        total = prompts * len(payload.engines)
        if progress is not None:
            if payload.generate_prompts:
                progress(PipelineProgress(phase=PipelinePhase.HARVEST, done=0, total=0))
            for done in range(total + 1):
                progress(
                    PipelineProgress(
                        phase=PipelinePhase.AUDIT, done=done, total=total, calls=done, message="x"
                    )
                )
            progress(
                PipelineProgress(phase=PipelinePhase.DONE, done=total, total=total, calls=total)
            )
        if self.db is not None:  # persist a snapshot so the planner sees it next time
            for custom in payload.custom_prompts:
                pid = prompt_id_for(payload.client.lob, custom.prompt_text)
                self.db.upsert_prompt(
                    MasterPromptRecord(
                        prompt_id=pid,
                        lob=payload.client.lob,
                        subtopic="S",
                        core_keyword="k",
                        search_volume=0,
                        prompt_text=custom.prompt_text,
                        search_intent=SearchIntent.INFORMATIONAL,
                        decision_stage=DecisionStage.AWARENESS,
                        prompt_type=PromptType.NON_BRANDED,
                        web_triggers=True,
                        verdict=Verdict.KEEP,
                        verdict_reason="x",
                    ),
                    payload.client.brand_name,
                )
                for engine in payload.engines:
                    self.db.record_snapshot(
                        pid,
                        CitationSnapshot(
                            engine=engine,
                            model="m",
                            captured_at=NOW,
                            samples=1,
                            web_trigger_rate=1.0,
                            client_cited_samples=1,
                            client_citation_rate=1.0,
                            client_cited=True,
                            client_best_rank=1,
                        ),
                        f"run-{len(self.payloads)}",
                    )
        if self.status is not ExecutionStatus.SUCCESS:
            return ToolResult[BaseModel](status=self.status, tool="t", error="denied")
        return ToolResult[BaseModel](
            status=self.status,
            tool="t",
            data=Summary(run_id=f"run-{len(self.payloads)}", warnings=["w"]),
        )


@pytest.fixture
def clock():
    class Clock:
        now = NOW

        def __call__(self):
            return self.now

    return Clock()


def test_engine_options_labels():
    values = [o["value"] for o in engine_options()]
    assert values == [e.value for e in Engine]
    assert engine_options()[0]["label"] == "Google AI Overview"


def test_run_builds_one_payload_per_batch_with_verbatim_prompts(
    store, db, project, prompts, settings, clock
):
    fake = FakePipeline(db=db)
    runner = ProjectRunner(store, db, settings=settings, pipeline=fake, clock=clock)
    outcome = runner.run(project.id)
    assert outcome.batches == 1
    assert outcome.prompts_run == 2
    assert outcome.statuses == ["success"]
    assert outcome.run_ids == ["run-1"]
    assert outcome.warnings == ["w"]
    payload = fake.payloads[0]
    assert payload.generate_prompts is False
    assert payload.engines == list(Engine)
    assert [c.prompt_text for c in payload.custom_prompts][0] == prompts[
        1
    ].prompt_text  # important first
    assert payload.client.brand_name == "GEP"
    # Second run: nothing due.
    again = runner.run(project.id)
    assert again.batches == 0 and again.reason == "nothing due"
    assert len(fake.payloads) == 1


def test_prompt_level_platforms_and_samples_split_batches(
    store, db, project, prompts, settings, clock
):
    store.update_prompt(
        project.id,
        prompts[1].id,
        TrackedPromptUpdate(engines=[Engine.PERPLEXITY], samples_per_engine=5),
    )
    fake = FakePipeline(db=db)
    outcome = ProjectRunner(store, db, settings=settings, pipeline=fake, clock=clock).run(
        project.id
    )
    assert outcome.batches == 2
    by_engines = {tuple(p.engines): p for p in fake.payloads}
    assert by_engines[(Engine.PERPLEXITY,)].samples_per_engine == 5
    assert len(by_engines[tuple(Engine)].custom_prompts) == 1


def test_progress_is_folded_across_batches(store, db, project, prompts, settings, clock):
    store.update_prompt(project.id, prompts[1].id, TrackedPromptUpdate(engines=[Engine.PERPLEXITY]))
    fake = FakePipeline(db=db)
    events: list[RunProgress] = []
    ProjectRunner(store, db, settings=settings, pipeline=fake, clock=clock).run(
        project.id, progress=events.append
    )
    assert events[0].phase == "planning" and events[0].batches_total == 2
    assert events[0].checks_total == 1 + len(Engine)  # 1 prompt × 1 platform + 1 prompt × 4
    assert all(e.checks_total == 1 + len(Engine) for e in events)
    done = [e.checks_done for e in events]
    assert done == sorted(done)  # monotonic
    assert events[-1].phase == "done" and events[-1].percent == 100.0
    assert events[-1].checks_done == 1 + len(Engine)
    assert events[-1].batches_done == 2
    assert events[-1].engine_calls == 1 + len(Engine)  # per-batch call counts are summed
    assert any(e.message.startswith("Batch 1/2") for e in events)
    assert any(e.message.startswith("Batch 2/2") for e in events)


def test_progress_grows_for_generation_batch(store, db, project, prompts, settings, clock):
    store.update_project(project.id, ProjectUpdate(generate_prompts=True))
    fake = FakePipeline(db=db)
    events: list[RunProgress] = []
    ProjectRunner(store, db, settings=settings, pipeline=fake, clock=clock).run(
        project.id, progress=events.append
    )
    assert events[0].batches_total == 2
    assert events[0].checks_total == 2 * len(Engine)  # generation size unknown at planning
    harvest = next(e for e in events if e.phase == "harvest")
    assert harvest.batches_done == 1 and harvest.percent <= 99.0  # bar never reads 100% early
    assert events[-1].checks_total == 2 * len(Engine) + 20 * len(Engine)
    assert events[-1].checks_done == events[-1].checks_total and events[-1].percent == 100.0
    assert any(e.phase == "harvest" for e in events)


def test_no_progress_sink_is_fine(store, db, project, prompts, settings, clock):
    outcome = ProjectRunner(
        store, db, settings=settings, pipeline=FakePipeline(db=db), clock=clock
    ).run(project.id, progress=None)
    assert outcome.batches == 1


def test_run_request_filters_and_force(store, db, project, prompts, settings, clock):
    fake = FakePipeline(db=db)
    runner = ProjectRunner(store, db, settings=settings, pipeline=fake, clock=clock)
    outcome = runner.run(
        project.id, RunRequest(prompt_ids=[prompts[0].id], engines=[Engine.GEMINI])
    )
    assert outcome.prompts_run == 1
    assert fake.payloads[0].engines == [Engine.GEMINI]
    forced = runner.run(project.id, RunRequest(force=True, prompt_ids=[prompts[0].id]))
    assert forced.batches == 1  # ran again despite fresh snapshot


def test_failed_status_is_reported(store, db, project, prompts, settings, clock):
    fake = FakePipeline(status=ExecutionStatus.BLOCKED_PENDING_APPROVAL)
    outcome = ProjectRunner(store, db, settings=settings, pipeline=fake, clock=clock).run(
        project.id
    )
    assert outcome.statuses == ["blocked_pending_approval"]
    assert outcome.run_ids == []
    assert any("denied" in w for w in outcome.warnings)


def test_generation_runs_at_project_interval(store, db, project, prompts, settings, clock):
    store.update_project(project.id, ProjectUpdate(generate_prompts=True, interval="weekly"))
    fake = FakePipeline(db=db)
    runner = ProjectRunner(store, db, settings=settings, pipeline=fake, clock=clock)
    outcome = runner.run(project.id)
    assert outcome.batches == 2
    assert any(p.generate_prompts for p in fake.payloads)
    clock.now = NOW + timedelta(days=3)
    outcome = runner.run(project.id)
    assert not any(p.generate_prompts for p in fake.payloads[2:])  # not yet weekly
    clock.now = NOW + timedelta(days=8)
    runner.run(project.id)
    assert sum(p.generate_prompts for p in fake.payloads) == 2
    # generation is skipped when the request targets specific prompts
    runner.run(project.id, RunRequest(force=True, prompt_ids=[prompts[0].id]))
    assert sum(p.generate_prompts for p in fake.payloads) == 2


def test_run_due_all_skips_disabled_projects(store, db, project, prompts, settings, clock):
    from src.modules.control_plane.schemas import ProjectCreate
    from tests.modules.control_plane.conftest import CLIENT

    paused = store.create_project(ProjectCreate(name="paused", client=CLIENT, enabled=False))
    fake = FakePipeline(db=db)
    outcomes = ProjectRunner(store, db, settings=settings, pipeline=fake, clock=clock).run_due_all()
    assert [o.project_id for o in outcomes] == [project.id]
    assert paused.enabled is False


def test_results_view(store, db, project, prompts, settings, clock):
    fake = FakePipeline(db=db)
    runner = ProjectRunner(store, db, settings=settings, pipeline=fake, clock=clock)
    empty = runner.results(project.id)
    assert all(v is None for r in empty for v in r.snapshots.values())
    assert all(set(r.due_on) == set(Engine) for r in empty)
    runner.run(project.id)
    results = runner.results(project.id)
    assert results[0].prompt.important is True
    assert results[0].effective_interval == "2d"
    assert all(s is not None and s.client_cited for r in results for s in r.snapshots.values())
    assert all(r.due_on == [] for r in results)
    assert results[0].organic_prompt is None


def test_default_pipeline_is_the_real_tool(store, db, project, settings, strict_guardrails):
    runner = ProjectRunner(store, db, settings=settings, guardrails=strict_guardrails)
    result = runner._default_pipeline(
        PipelineInput(client=project.client, custom_prompts=[], generate_prompts=False), None
    )
    assert result.status is ExecutionStatus.BLOCKED_PENDING_APPROVAL
