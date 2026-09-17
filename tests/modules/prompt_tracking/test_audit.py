"""Tests for adaptive sampling, call reservation and circuit awareness in the audit step."""

from __future__ import annotations

import pytest

from src.core.errors import BudgetExceededError, IntegrationError
from src.core.rate_limiter import CostLedger
from src.integrations.schemas import Citation, Engine, EngineAnswer
from src.modules.prompt_tracking.audit import CallCapReached, RunPlan, audit_engine, consensus
from src.modules.prompt_tracking.schemas import (
    ClientProfile,
    DecisionStage,
    IntentAction,
    IntentDecision,
    PromptCandidate,
    PromptType,
    SearchIntent,
)

CITED = [Citation(url="https://www.gep.com/x", domain="gep.com", position=1)]
NOT_CITED = [Citation(url="https://coupa.com/", domain="coupa.com", position=1)]


def _answer(citations, *, response_id: str | None = None) -> EngineAnswer:
    return EngineAnswer(
        engine=Engine.PERPLEXITY,
        model="sonar-pro",
        prompt="p",
        answer_text="text",
        web_triggered=True,
        citations=citations,
        response_id=response_id,
    )


class ScriptedEngine:
    """Returns scripted answers in order; `available` is settable."""

    def __init__(self, script, *, available: bool = True) -> None:
        self.script = list(script)
        self.available = available
        self.calls = 0

    def ask(self, prompt: str) -> EngineAnswer:
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def client() -> ClientProfile:
    return ClientProfile(
        brand_name="GEP",
        domains=["gep.com"],
        competitor_domains=["coupa.com"],
        lob="L",
        seed_keywords=["procurement software"],
    )


@pytest.fixture
def candidate() -> PromptCandidate:
    return PromptCandidate(
        prompt_text="What is procurement software?",
        core_keyword="procurement software",
        search_volume=10,
        subtopic="S",
        prompt_type=PromptType.NON_BRANDED,
        search_intent=SearchIntent.INFORMATIONAL,
        decision_stage=DecisionStage.AWARENESS,
        intent=IntentDecision(
            prompt_text="What is procurement software?",
            action=IntentAction.KEEP,
            score=0.8,
            entity_type="x",
            reason="y",
        ),
    )


def _plan(**kw) -> RunPlan:
    defaults = {"max_calls": 0, "adaptive": True, "min_samples": 2}
    defaults.update(kw)
    return RunPlan(CostLedger(ceiling_usd=100.0), **defaults)


class TestConsensus:
    def test_agreement_and_disagreement(self):
        assert consensus([_answer(CITED), _answer(CITED)], ["gep.com"]) is True
        assert consensus([_answer(NOT_CITED), _answer(NOT_CITED)], ["gep.com"]) is True
        assert consensus([_answer(CITED), _answer(NOT_CITED)], ["gep.com"]) is False


class TestAdaptiveSampling:
    def test_stops_early_when_samples_agree(self, client, candidate):
        engine = ScriptedEngine(
            [_answer(CITED, response_id="r1"), _answer(CITED, response_id="r2")]
        )
        outcome = audit_engine(
            engine, Engine.PERPLEXITY, candidate, client, samples=5, cost_each=0.01, plan=_plan()
        )
        assert outcome.calls == 2
        assert engine.calls == 2
        assert outcome.snapshot.samples == 2
        assert outcome.snapshot.client_citation_rate == 1.0
        assert outcome.snapshot.response_ids == ["r1", "r2"]
        assert outcome.cost == pytest.approx(0.02)

    def test_continues_to_the_maximum_when_samples_disagree(self, client, candidate):
        engine = ScriptedEngine([_answer(CITED), _answer(NOT_CITED), _answer(CITED)])
        outcome = audit_engine(
            engine, Engine.PERPLEXITY, candidate, client, samples=3, cost_each=0.01, plan=_plan()
        )
        assert outcome.calls == 3
        assert outcome.snapshot.client_cited_samples == 2

    def test_fixed_mode_takes_every_sample(self, client, candidate):
        engine = ScriptedEngine([_answer(CITED)] * 4)
        outcome = audit_engine(
            engine,
            Engine.PERPLEXITY,
            candidate,
            client,
            samples=4,
            cost_each=0.01,
            plan=_plan(adaptive=False),
        )
        assert outcome.calls == 4

    def test_min_samples_is_respected(self, client, candidate):
        engine = ScriptedEngine([_answer(CITED)] * 3)
        outcome = audit_engine(
            engine,
            Engine.PERPLEXITY,
            candidate,
            client,
            samples=3,
            cost_each=0.01,
            plan=_plan(min_samples=3),
        )
        assert outcome.calls == 3

    def test_failures_do_not_count_toward_consensus(self, client, candidate):
        engine = ScriptedEngine([IntegrationError("e", "down"), _answer(CITED), _answer(CITED)])
        outcome = audit_engine(
            engine, Engine.PERPLEXITY, candidate, client, samples=5, cost_each=0.01, plan=_plan()
        )
        assert outcome.calls == 3
        assert outcome.failures == 1
        assert outcome.snapshot.samples == 3
        assert outcome.snapshot.failed_samples == 1


class TestReservation:
    def test_call_cap_after_a_paid_sample_keeps_it_and_reports_the_stop(self, client, candidate):
        engine = ScriptedEngine([_answer(CITED)] * 5)
        plan = _plan(max_calls=1, adaptive=False)
        outcome = audit_engine(
            engine, Engine.PERPLEXITY, candidate, client, samples=3, cost_each=0.01, plan=plan
        )
        assert engine.calls == 1
        assert plan.calls == 1
        assert outcome.calls == 1
        assert outcome.snapshot.client_cited is True
        assert outcome.stop_reason is not None
        assert "cap of 1" in outcome.stop_reason

    def test_budget_exhaustion_after_a_paid_sample_is_reported(self, client, candidate):
        engine = ScriptedEngine([_answer(CITED)] * 5)
        plan = RunPlan(CostLedger(ceiling_usd=0.015), adaptive=False)
        outcome = audit_engine(
            engine, Engine.PERPLEXITY, candidate, client, samples=3, cost_each=0.01, plan=plan
        )
        assert engine.calls == 1
        assert outcome.stop_reason is not None
        assert "refused" in outcome.stop_reason

    def test_refusal_of_the_first_sample_raises(self, client, candidate):
        engine = ScriptedEngine([_answer(CITED)] * 5)
        with pytest.raises(BudgetExceededError):
            audit_engine(
                engine,
                Engine.PERPLEXITY,
                candidate,
                client,
                samples=3,
                cost_each=0.01,
                plan=RunPlan(CostLedger(ceiling_usd=0.0)),
            )
        capped = _plan(max_calls=1)
        capped.reserve(0.0)  # cap already consumed elsewhere in the run
        with pytest.raises(CallCapReached):
            audit_engine(
                engine, Engine.PERPLEXITY, candidate, client, samples=3, cost_each=0.01, plan=capped
            )
        assert engine.calls == 0


class TestCircuitAwareness:
    def test_open_breaker_skips_all_samples_at_no_cost(self, client, candidate):
        engine = ScriptedEngine([], available=False)
        plan = _plan()
        outcome = audit_engine(
            engine, Engine.PERPLEXITY, candidate, client, samples=3, cost_each=0.01, plan=plan
        )
        assert outcome.calls == 0
        assert outcome.cost == 0.0
        assert outcome.circuit_refused == 3
        assert plan.refused == 3
        assert outcome.snapshot.samples == 1
        assert outcome.snapshot.failed_samples == 1
        assert outcome.snapshot.model == "unavailable"

    def test_post_process_hook_runs_per_answer(self, client, candidate):
        engine = ScriptedEngine([_answer(CITED), _answer(CITED)])
        seen: list[str] = []
        audit_engine(
            engine,
            Engine.PERPLEXITY,
            candidate,
            client,
            samples=2,
            cost_each=0.01,
            plan=_plan(),
            post_process=lambda a: seen.append(a.model),
        )
        assert seen == ["sonar-pro", "sonar-pro"]
