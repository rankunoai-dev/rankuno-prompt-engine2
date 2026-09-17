"""Progress events emitted by the pipeline while it runs."""

from __future__ import annotations

import pytest

from src.integrations.schemas import Engine
from src.modules.prompt_tracking.schemas import (
    ClientProfile,
    CustomPrompt,
    PipelineInput,
    PipelinePhase,
    PipelineProgress,
)
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB
from tests.modules.prompt_tracking.test_pipeline import LOB, SEED, StubEngine, _pipeline


@pytest.fixture
def client() -> ClientProfile:
    return ClientProfile(
        brand_name="GEP",
        aliases=["GEP SMART"],
        domains=["gep.com"],
        competitor_domains=["coupa.com"],
        lob=LOB,
        seed_keywords=[SEED],
    )


@pytest.fixture
def db(tmp_path) -> TimeSeriesDB:
    return TimeSeriesDB(tmp_path / "tracker.sqlite")


PROMPTS = [
    CustomPrompt(prompt_text="What is the best procurement software?"),
    CustomPrompt(prompt_text="Is GEP SMART good for source to pay?"),
    CustomPrompt(prompt_text="How much does procurement software cost?"),
]


def _run(permissive_guardrails, ledger, settings, client, db, sink, **payload):
    engines = {e: StubEngine(e) for e in Engine}
    tool = _pipeline(permissive_guardrails, ledger, settings, engines=engines, db=db, progress=sink)
    result = tool.run(
        PipelineInput(
            client=client,
            custom_prompts=PROMPTS,
            generate_prompts=False,
            samples_per_engine=2,
            **payload,
        )
    )
    assert result.ok, result.error
    return result.data


def test_events_cover_every_check_and_end_done(permissive_guardrails, ledger, settings, client, db):
    events: list[PipelineProgress] = []
    summary = _run(permissive_guardrails, ledger, settings, client, db, events.append)

    phases = [e.phase for e in events]
    assert PipelinePhase.HARVEST not in phases  # custom prompts only: no Semrush
    assert phases[0] is PipelinePhase.AUDIT
    assert phases[-1] is PipelinePhase.DONE
    assert PipelinePhase.KEYWORD_RANKS in phases and PipelinePhase.REPORT in phases

    audits = [e for e in events if e.phase is PipelinePhase.AUDIT]
    total = len(PROMPTS) * len(Engine)
    assert audits[0].done == 0 and audits[0].total == total
    assert [e.done for e in audits] == list(range(total + 1))  # one event per finished check
    assert all(e.total == total for e in audits)
    assert audits[-1].calls == summary.engine_calls
    assert "on " in audits[1].message  # names the prompt and platform just finished
    assert events[-1].calls == summary.engine_calls
    assert summary.run_id in events[-1].message


def test_reused_snapshots_count_as_done_immediately(
    permissive_guardrails, ledger, settings, client, db
):
    _run(permissive_guardrails, ledger, settings, client, db, None)
    events: list[PipelineProgress] = []
    summary = _run(
        permissive_guardrails, ledger, settings, client, db, events.append, reuse_within_hours=48
    )
    assert summary.reused_snapshots == len(PROMPTS) * len(Engine)
    first_audit = next(e for e in events if e.phase is PipelinePhase.AUDIT)
    assert first_audit.done == first_audit.total == summary.reused_snapshots


def test_harvest_phase_emitted_when_generating(permissive_guardrails, ledger, settings, client, db):
    events: list[PipelineProgress] = []
    engines = {e: StubEngine(e) for e in Engine}
    tool = _pipeline(
        permissive_guardrails, ledger, settings, engines=engines, db=db, progress=events.append
    )
    result = tool.run(PipelineInput(client=client, samples_per_engine=1))
    assert result.ok, result.error
    assert events[0].phase is PipelinePhase.HARVEST and events[0].total == 0
    audit = next(e for e in events if e.phase is PipelinePhase.AUDIT)
    assert audit.total == 20 * len(Engine)


def test_failing_sink_does_not_abort_the_run(permissive_guardrails, ledger, settings, client, db):
    calls = 0

    def boom(_: PipelineProgress) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("ui went away")

    summary = _run(permissive_guardrails, ledger, settings, client, db, boom)
    assert calls > 0
    assert summary.prompts_selected == len(PROMPTS)


def test_skip_engine_audit_emits_no_audit_events(
    permissive_guardrails, ledger, settings, client, db
):
    events: list[PipelineProgress] = []
    _run(permissive_guardrails, ledger, settings, client, db, events.append, skip_engine_audit=True)
    assert [e.phase for e in events] == [PipelinePhase.REPORT, PipelinePhase.DONE]
    assert events[-1].done == events[-1].total == 0


@pytest.mark.parametrize("phase", list(PipelinePhase))
def test_phase_values_are_stable_strings(phase: PipelinePhase):
    assert phase.value == phase.value.lower()
    assert PipelineProgress(phase=phase, done=0, total=0).model_dump()["phase"] == phase
