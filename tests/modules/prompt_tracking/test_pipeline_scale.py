"""Pipeline tests for cycle 0003: custom prompts, reuse, call cap, concurrency,
adaptive sampling, circuit awareness, model-shift detection and sample storage."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from src.core.rate_limiter import CostLedger
from src.integrations.schemas import Citation, Engine, EngineAnswer
from src.modules.prompt_tracking.schemas import CustomPrompt, PipelineInput
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB
from tests.modules.prompt_tracking.test_pipeline import (
    LOB,
    SEED,
    StubEngine,
    _pipeline,
    _semrush,
    client,
)

__all__ = ["client"]

CUSTOM = [
    CustomPrompt(prompt_text="Is GEP SMART the right choice for a mid-size manufacturer?"),
    CustomPrompt(
        prompt_text="Which source to pay platforms integrate with SAP?",
        keyword="source to pay software",
        subtopic="Integrations",
    ),
    CustomPrompt(prompt_text="procurement software jobs in texas"),  # would fail the gate
]


class ModelStub(StubEngine):
    """Stub whose reported model string and availability can be set."""

    def __init__(self, engine: Engine, *, model: str = "stub-model", available: bool = True):
        super().__init__(engine)
        self.model = model
        self.available = available
        self.threads: set[str] = set()
        self._lock = threading.Lock()

    def ask(self, prompt: str) -> EngineAnswer:
        with self._lock:
            self.calls += 1
            self.threads.add(threading.current_thread().name)
        return EngineAnswer(
            engine=self.engine,
            model=self.model,
            prompt=prompt,
            answer_text="answer",
            web_triggered=True,
            citations=[Citation(url="https://www.gep.com/x", domain="gep.com", position=1)],
            response_id=f"resp-{self.calls}",
        )


def _count(db_path: Path, sql: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute(sql).fetchone()[0])
    finally:
        conn.close()


@pytest.fixture
def db(tmp_path) -> TimeSeriesDB:
    return TimeSeriesDB(tmp_path / "db" / "tracker.sqlite")


@pytest.fixture
def ledger() -> CostLedger:
    return CostLedger(ceiling_usd=100.0)


class TestCustomPrompts:
    def test_custom_only_skips_semrush_and_tracks_every_prompt(
        self, permissive_guardrails, ledger, settings, client, db
    ):
        engines = {Engine.PERPLEXITY: ModelStub(Engine.PERPLEXITY)}
        tool = _pipeline(
            permissive_guardrails,
            ledger,
            settings,
            engines=engines,
            db=db,
            semrush=_semrush(settings, status=500),  # would warn if called
        )
        result = tool.run(
            PipelineInput(
                client=client,
                engines=[Engine.PERPLEXITY],
                samples_per_engine=1,
                custom_prompts=CUSTOM,
                generate_prompts=False,
            )
        )
        assert result.ok, result.error
        summary = result.data
        assert summary.warnings == []
        assert summary.semrush_units == 0
        assert summary.custom_prompts == 3
        assert summary.prompts_selected == 3
        assert summary.candidates_generated == 3
        texts = [r.prompt_text for r in summary.records]
        assert texts[0] == "Is GEP SMART the right choice for a mid-size manufacturer?"
        assert texts[2] == "procurement software jobs in texas"  # byte-exact, still tracked
        by_text = {r.prompt_text: r for r in summary.records}
        second = by_text["Which source to pay platforms integrate with SAP?"]
        assert second.core_keyword == "source to pay software"
        assert second.subtopic == "Integrations"
        assert by_text[texts[0]].prompt_type.value == "BRANDED"
        assert by_text[texts[0]].core_keyword == SEED
        assert summary.engine_calls == 3

    def test_custom_plus_generated(self, permissive_guardrails, ledger, settings, client, db):
        engines = {Engine.PERPLEXITY: ModelStub(Engine.PERPLEXITY)}
        tool = _pipeline(permissive_guardrails, ledger, settings, engines=engines, db=db)
        result = tool.run(
            PipelineInput(
                client=client,
                engines=[Engine.PERPLEXITY],
                samples_per_engine=1,
                custom_prompts=CUSTOM[:2],
                generate_prompts=True,
            )
        )
        assert result.ok, result.error
        assert result.data.custom_prompts == 2
        assert result.data.prompts_selected == 22
        assert result.data.semrush_units > 0
        assert "Analyst-supplied" not in result.data.records[-1].verdict_reason

    def test_describe_invocation_counts_custom_prompts(
        self, permissive_guardrails, ledger, settings, client, db
    ):
        tool = _pipeline(permissive_guardrails, ledger, settings, db=db)
        text = tool.describe_invocation(
            PipelineInput(client=client, custom_prompts=CUSTOM, generate_prompts=False)
        )
        assert "3 prompts" in text
        text = tool.describe_invocation(PipelineInput(client=client, custom_prompts=CUSTOM))
        assert "23 prompts" in text


class TestReuse:
    def test_second_run_within_window_makes_no_engine_calls(
        self, permissive_guardrails, ledger, settings, client, db, tmp_path
    ):
        engines = {Engine.PERPLEXITY: ModelStub(Engine.PERPLEXITY)}
        payload = PipelineInput(
            client=client,
            engines=[Engine.PERPLEXITY],
            samples_per_engine=1,
            custom_prompts=CUSTOM,
            generate_prompts=False,
            reuse_within_hours=24,
        )
        tool = _pipeline(permissive_guardrails, ledger, settings, engines=engines, db=db)
        first = tool.run(payload).data
        assert first.engine_calls == 3
        db_path = tmp_path / "db" / "tracker.sqlite"
        assert _count(db_path, "SELECT COUNT(*) FROM snapshots") == 3

        second = tool.run(payload).data
        assert second.engine_calls == 0
        assert second.reused_snapshots == 3
        assert all(s.reused for r in second.records for s in r.citation_history)
        assert second.records[0].citation_history[0].client_cited is True
        assert _count(db_path, "SELECT COUNT(*) FROM snapshots") == 3  # not re-recorded
        assert engines[Engine.PERPLEXITY].calls == 3

    def test_zero_window_always_calls(self, permissive_guardrails, ledger, settings, client, db):
        engines = {Engine.PERPLEXITY: ModelStub(Engine.PERPLEXITY)}
        payload = PipelineInput(
            client=client,
            engines=[Engine.PERPLEXITY],
            samples_per_engine=1,
            custom_prompts=CUSTOM,
            generate_prompts=False,
            reuse_within_hours=0,
        )
        tool = _pipeline(permissive_guardrails, ledger, settings, engines=engines, db=db)
        tool.run(payload)
        assert tool.run(payload).data.reused_snapshots == 0


class TestCallCap:
    def test_cap_stops_the_audit_and_keeps_records(
        self, permissive_guardrails, ledger, settings, client, db
    ):
        engines = {Engine.PERPLEXITY: ModelStub(Engine.PERPLEXITY)}
        tool = _pipeline(permissive_guardrails, ledger, settings, engines=engines, db=db)
        result = tool.run(
            PipelineInput(
                client=client, engines=[Engine.PERPLEXITY], samples_per_engine=1, max_engine_calls=7
            )
        )
        assert result.ok, result.error
        summary = result.data
        assert summary.engine_calls == 7
        assert summary.prompts_selected == 20
        assert sum(1 for r in summary.records if r.citation_history) == 7
        assert any("Engine call cap of 7" in w for w in summary.warnings)
        assert summary.keyword_rank_calls == 0  # skipped once the audit stopped


class TestAdaptiveSampling:
    def test_early_stop_saves_calls_when_answers_agree(
        self, permissive_guardrails, ledger, settings, client, db
    ):
        engines = {Engine.PERPLEXITY: ModelStub(Engine.PERPLEXITY)}
        tool = _pipeline(permissive_guardrails, ledger, settings, engines=engines, db=db)
        adaptive = tool.run(
            PipelineInput(client=client, engines=[Engine.PERPLEXITY], samples_per_engine=3)
        ).data
        assert adaptive.engine_calls == 20 * 2
        assert all(s.samples == 2 for r in adaptive.records for s in r.citation_history)

        fixed = tool.run(
            PipelineInput(
                client=client,
                engines=[Engine.PERPLEXITY],
                samples_per_engine=3,
                adaptive_sampling=False,
            )
        ).data
        assert fixed.engine_calls == 20 * 3


class TestConcurrency:
    def test_engine_calls_run_on_multiple_threads(
        self, permissive_guardrails, ledger, settings, client, db
    ):
        engines = {e: ModelStub(e) for e in (Engine.PERPLEXITY, Engine.GEMINI)}
        tool = _pipeline(permissive_guardrails, ledger, settings, engines=engines, db=db)
        result = tool.run(
            PipelineInput(
                client=client,
                engines=list(engines),
                samples_per_engine=1,
                max_workers=4,
                custom_prompts=CUSTOM,
                generate_prompts=False,
            )
        )
        assert result.ok, result.error
        assert result.data.engine_calls == 6
        threads = set().union(*(stub.threads for stub in engines.values()))
        assert len(threads) > 1
        assert all(len(r.citation_history) == 2 for r in result.data.records)


class TestCircuitAwareness:
    def test_open_breaker_refuses_calls_without_spend(
        self, permissive_guardrails, ledger, settings, client, db
    ):
        engines = {Engine.PERPLEXITY: ModelStub(Engine.PERPLEXITY, available=False)}
        tool = _pipeline(permissive_guardrails, ledger, settings, engines=engines, db=db)
        result = tool.run(
            PipelineInput(
                client=client,
                engines=[Engine.PERPLEXITY],
                samples_per_engine=2,
                custom_prompts=CUSTOM,
                generate_prompts=False,
            )
        )
        assert result.ok, result.error
        summary = result.data
        assert summary.engine_calls == 0
        assert summary.calls_refused_by_circuit == 6
        assert summary.estimated_cost_usd == 0.0
        assert all(s.model == "unavailable" for r in summary.records for s in r.citation_history)


class TestModelShiftAndSamples:
    def test_samples_are_stored_and_model_shift_is_reported(
        self, permissive_guardrails, ledger, settings, client, db, tmp_path
    ):
        payload = PipelineInput(
            client=client,
            engines=[Engine.PERPLEXITY],
            samples_per_engine=1,
            custom_prompts=CUSTOM,
            generate_prompts=False,
        )
        first_engines = {Engine.PERPLEXITY: ModelStub(Engine.PERPLEXITY, model="sonar-pro")}
        first = (
            _pipeline(permissive_guardrails, ledger, settings, engines=first_engines, db=db)
            .run(payload)
            .data
        )
        assert first.model_shifts == []
        db_path = tmp_path / "db" / "tracker.sqlite"
        assert _count(db_path, "SELECT COUNT(*) FROM answer_samples") == 3
        assert _count(db_path, "SELECT COUNT(*) FROM answer_samples WHERE client_cited = 1") == 3
        assert first.records[0].citation_history[0].response_ids == ["resp-1"]

        second_engines = {Engine.PERPLEXITY: ModelStub(Engine.PERPLEXITY, model="sonar-pro-2")}
        second = (
            _pipeline(permissive_guardrails, ledger, settings, engines=second_engines, db=db)
            .run(payload)
            .data
        )
        assert second.model_shifts == ["PERPLEXITY: sonar-pro -> sonar-pro-2"]
        assert db.last_model(Engine.PERPLEXITY, exclude_run_id="none") == "sonar-pro-2"
        assert LOB in {r.lob for r in second.records}
