"""The narrative: what the model is shown, and what it is not allowed to say."""

from __future__ import annotations

import json

from src.core.config import Settings
from src.integrations.anthropic_judge import StructuredReply
from src.modules.reporting.facts import build_fact_sheet
from src.modules.reporting.narrative import (
    allowed_numbers,
    compose,
    model_payload,
    template_narrative,
)
from src.modules.reporting.schemas import NarrativeSource


class FakeJudge:
    """Stands in for `AnthropicJudgeClient.classify`."""

    def __init__(self, data: dict[str, object] | None, *, stop: str = "end_turn") -> None:
        self.data = data
        self.stop = stop
        self.calls: list[dict[str, object]] = []

    def classify(self, **kwargs: object) -> StructuredReply:
        self.calls.append(kwargs)
        return StructuredReply(
            data=self.data,
            stop_reason=self.stop,
            model=str(kwargs.get("model") or "claude-sonnet-5"),
            input_tokens=900,
            output_tokens=300,
        )


def _settings(**kwargs: object) -> Settings:
    base: dict[str, object] = {
        "anthropic_api_key": "sk-test",
        "report_max_spend_usd": 0.25,
        "anthropic_report_model": "claude-sonnet-5",
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def _sheet(project, insights, positions, previous_positions):
    return build_fact_sheet(
        project, insights, positions, brand=project.brand, previous_positions=previous_positions
    )


def test_the_model_sees_numbers_and_labels_but_no_engine_prose(
    project, insights, positions, previous_positions
):
    """No answer text, no citation bodies: nothing to be injected by."""
    facts = _sheet(project, insights, positions, previous_positions)
    payload = json.dumps(model_payload(facts))
    assert "GEP" in payload and "Citation rate" in payload
    assert "implementation timelines run long" not in payload  # the sentiment quote
    assert "https://" not in payload


def test_a_clean_reply_is_kept_and_its_spend_recorded(
    project, insights, positions, previous_positions
):
    """Every figure exists in the facts, so nothing falls back."""
    facts = _sheet(project, insights, positions, previous_positions)
    judge = FakeJudge(
        {
            "headline": "GEP cited in 25% of AI answers",
            # 63 of 120 answers in the prior window is 52.5%, which the report prints as 52%.
            "summary": "Citations fell to 25% this window from 52% in the previous one.",
            "wins": ["ChatGPT Search still cites the brand in 50% of answers."],
            "risks": ["Gemini cited the brand in 0% of answers."],
            "next_steps": ["Correct the record on the cited review page."],
        }
    )
    narrative = compose(facts, client=judge, settings=_settings())  # type: ignore[arg-type]
    assert narrative.source is NarrativeSource.MODEL
    assert narrative.rejected_sections == []
    assert narrative.model == "claude-sonnet-5"
    assert narrative.spend_usd > 0
    assert judge.calls[0]["model"] == "claude-sonnet-5"


def test_an_invented_number_is_refused_and_the_template_takes_over(
    project, insights, positions, previous_positions
):
    """The whole point: a figure the engine never measured cannot reach a client."""
    facts = _sheet(project, insights, positions, previous_positions)
    judge = FakeJudge(
        {
            "headline": "GEP cited in 25% of AI answers",
            "summary": "Visibility grew 400% and the brand now leads 87% of answers.",
            "wins": ["Traffic rose 32% after the last change."],
            "risks": ["Gemini cited the brand in 0% of answers."],
            "next_steps": ["Correct the record on the cited review page."],
        }
    )
    narrative = compose(facts, client=judge, settings=_settings())  # type: ignore[arg-type]
    assert narrative.source is NarrativeSource.MIXED
    assert "summary" in narrative.rejected_sections
    assert "wins" in narrative.rejected_sections
    assert "400%" not in narrative.summary
    assert "32%" not in " ".join(narrative.wins)
    assert narrative.headline == "GEP cited in 25% of AI answers"  # this one checked out


def test_a_url_in_the_prose_is_refused(project, insights, positions, previous_positions):
    """The model may not cite; the report's own sections carry the links."""
    facts = _sheet(project, insights, positions, previous_positions)
    judge = FakeJudge(
        {
            "headline": "Visibility update",
            "summary": "See https://example.com/analysis for the detail.",
            "wins": [],
            "risks": [],
            "next_steps": [],
        }
    )
    narrative = compose(facts, client=judge, settings=_settings())  # type: ignore[arg-type]
    assert "summary" in narrative.rejected_sections
    assert "example.com" not in narrative.summary


def test_no_key_no_budget_and_a_refusal_all_fall_back_to_templates(
    project, insights, positions, previous_positions
):
    """A report always renders, whatever the vendor does."""
    facts = _sheet(project, insights, positions, previous_positions)
    template = template_narrative(facts)

    no_key = compose(facts, client=FakeJudge({}), settings=_settings(anthropic_api_key=None))  # type: ignore[arg-type]
    assert no_key.source is NarrativeSource.TEMPLATE
    assert no_key.summary == template.summary

    no_budget = compose(facts, client=FakeJudge({}), settings=_settings(report_max_spend_usd=0))  # type: ignore[arg-type]
    assert no_budget.source is NarrativeSource.TEMPLATE

    refused = compose(
        facts,
        client=FakeJudge(None, stop="refusal"),  # type: ignore[arg-type]
        settings=_settings(),
    )
    assert refused.source is NarrativeSource.TEMPLATE

    disabled = compose(facts, client=FakeJudge({}), settings=_settings(), enabled=False)  # type: ignore[arg-type]
    assert disabled.source is NarrativeSource.TEMPLATE


def test_the_template_states_the_window_and_the_low_confidence_caveat(
    project, insights, positions, previous_positions
):
    """Deterministic prose is still honest prose."""
    facts = _sheet(project, insights, positions, previous_positions)
    words = template_narrative(facts)
    assert "25%" in words.headline
    assert "120 sampled answer(s)" in words.summary
    assert "early signals" not in words.summary

    facts.window.low_confidence = True
    caveated = template_narrative(facts)
    assert "early signals" in caveated.summary


def test_allowed_numbers_cover_every_printed_figure(
    project, insights, positions, previous_positions
):
    """The whitelist is derived from the same payload the model is given."""
    facts = _sheet(project, insights, positions, previous_positions)
    allowed = allowed_numbers(facts)
    assert "25" in allowed  # pooled citation rate
    assert "50" in allowed  # ChatGPT's own rate
    assert "120" in allowed  # answers sampled
    assert "87" not in allowed
