"""The runner's judging phase (ADR 0021): runs after samples, never fails the crawl."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from src.core.errors import IntegrationError
from src.integrations.anthropic_judge import StructuredReply
from src.integrations.schemas import Engine
from src.modules.control_plane.runner import ProjectRunner
from src.modules.control_plane.schemas import ProjectCreate, ProjectUpdate, RunRequest
from src.modules.prompt_tracking.schemas import AnswerSample, MentionSnippet, prompt_id_for
from tests.modules.control_plane.conftest import CLIENT, NOW
from tests.modules.control_plane.test_runner import FakePipeline


class SamplingPipeline(FakePipeline):
    """The fake pipeline, plus one stored answer sample with mentions per prompt."""

    def __call__(self, payload, progress=None):
        result = super().__call__(payload, progress)
        run_id = f"run-{len(self.payloads)}"
        for custom in payload.custom_prompts:
            pid = prompt_id_for(payload.client.lob, custom.prompt_text)
            self.db.record_samples(
                pid,
                [
                    AnswerSample(
                        prompt_id=pid,
                        run_id=run_id,
                        engine=engine,
                        model="m",
                        captured_at=NOW,
                        web_triggered=True,
                        client_cited=True,
                        cited_domains=["gep.com"],
                        answer_excerpt="GEP is fine.",
                        answer_text="GEP is fine. Coupa is pricey.",
                        mention_detected=True,
                        mentions=[
                            MentionSnippet(entity="client", term="GEP", snippet="GEP is fine."),
                        ],
                    )
                    for engine in payload.engines
                ],
                run_id,
            )
        return result


class FakeJudge:
    model = "claude-haiku-4-5"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def classify(self, *, system, user, schema, max_tokens, operation) -> StructuredReply:
        self.calls += 1
        if self.fail:
            raise IntegrationError("anthropic", "down")
        n = user.count("### item ")
        return StructuredReply(
            data={
                "items": [
                    {"id": i, "polarity": "positive", "attributes": [], "confidence": 0.8}
                    for i in range(1, n + 1)
                ]
            },
            stop_reason="end_turn",
            model=self.model,
        )


@pytest.fixture
def project_with_prompt(store):
    project = store.create_project(
        ProjectCreate(name="GEP", client=CLIENT, engines=[Engine.PERPLEXITY])
    )
    from src.modules.control_plane.schemas import TrackedPromptCreate

    store.add_prompt(project.id, TrackedPromptCreate(prompt_text="best procurement suite?"))
    return project


def _run(store, db, settings, judge: Any, project_id: str):
    fake = SamplingPipeline(db=db)
    phases: list[str] = []
    runner = ProjectRunner(
        store, db, settings=settings, pipeline=fake, clock=lambda: NOW, judge=judge
    )
    outcome = runner.run(
        project_id, RunRequest(force=True), progress=lambda p: phases.append(p.phase)
    )
    return outcome, phases


def test_judging_runs_after_samples_and_stores_rows(store, db, settings, project_with_prompt):
    judge = FakeJudge()
    outcome, phases = _run(store, db, settings, judge, project_with_prompt.id)
    assert outcome.batches == 1 and judge.calls == 1
    assert "judging" in phases
    # after the last pipeline batch, before the run-level done that closes the run
    assert phases.index("judging") > max(i for i, p in enumerate(phases) if p == "audit")
    assert phases[-1] == "done" and phases.index("judging") < len(phases) - 1
    rows = db.judgements_for(run_ids=list(outcome.run_ids))
    assert len(rows) == 1 and rows[0].polarity == "positive"
    assert not any("Sentiment" in w for w in outcome.warnings)  # nothing unscored


def test_judge_failure_never_fails_the_crawl(store, db, settings, project_with_prompt):
    outcome, _ = _run(store, db, settings, FakeJudge(fail=True), project_with_prompt.id)
    assert outcome.batches == 1 and outcome.project_run_id is not None
    rows = db.judgements_for(run_ids=list(outcome.run_ids))
    assert {r.status for r in rows} == {"unscored"}
    assert any("unscored" in w for w in outcome.warnings)


def test_no_key_and_opted_out_projects_skip_judging(store, db, settings, project_with_prompt):
    # No key configured and no injected judge: skipped, crawl unaffected.
    assert settings.anthropic_api_key is None
    outcome, phases = _run(store, db, settings, None, project_with_prompt.id)
    assert outcome.batches == 1 and "judging" not in phases
    assert db.judgements_for(run_ids=list(outcome.run_ids)) == []

    # A project that opted out is skipped even with a judge available.
    store.update_project(project_with_prompt.id, ProjectUpdate(sentiment=False))
    judge = FakeJudge()
    outcome, phases = _run(store, db, settings, judge, project_with_prompt.id)
    assert judge.calls == 0 and "judging" not in phases


def test_key_without_injected_judge_builds_the_real_client(
    store, db, settings, project_with_prompt
):
    keyed = settings.model_copy(update={"anthropic_api_key": SecretStr("k")})
    runner = ProjectRunner(
        store, db, settings=keyed, pipeline=SamplingPipeline(db=db), clock=lambda: NOW
    )
    assert type(runner._resolve_judge()).__name__ == "AnthropicJudgeClient"
    zero = keyed.model_copy(update={"sentiment_max_sentences_per_run": 0})
    runner = ProjectRunner(
        store, db, settings=zero, pipeline=SamplingPipeline(db=db), clock=lambda: NOW
    )
    assert runner._resolve_judge() is None
