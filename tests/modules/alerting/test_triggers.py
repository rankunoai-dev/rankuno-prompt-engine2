"""The rules: what fires, and — more importantly — what does not."""

from __future__ import annotations

from datetime import UTC, datetime

from src.integrations.schemas import Engine
from src.modules.alerting.schemas import AlertRule, AlertSeverity
from src.modules.alerting.triggers import MIN_TRIALS, engine_totals, evaluate
from src.modules.control_plane.schemas import (
    ActionCard,
    ActionEvidence,
    ConsolidatedPosition,
    EngineHealth,
    EvidenceQuote,
    InsightBasis,
    InsightChange,
    InsightsView,
)

NOW = datetime(2026, 9, 24, tzinfo=UTC)
ALL_RULES = set(AlertRule)


def position(engine: Engine, *, samples: int, cited: int, prompt_id: str = "p1"):
    """One consolidated row with the counts the rules pool."""
    return ConsolidatedPosition(
        prompt_id=prompt_id,
        engine=engine,
        runs=3,
        first_run_at=NOW,
        last_run_at=NOW,
        samples=samples,
        failed_samples=0,
        cited_samples=cited,
        citation_rate=cited / samples if samples else 0.0,
        cited=cited > 0,
        mention_samples=cited,
        mention_rate=cited / samples if samples else 0.0,
        mentioned=cited > 0,
    )


def view(*, changes=None, actions=None, engines=(Engine.CHATGPT_SEARCH,)) -> InsightsView:
    """A minimal insights view carrying only what a rule reads."""
    return InsightsView(
        generated_at=NOW,
        basis=InsightBasis(
            consolidation_id="c2",
            computed_from="consolidation",
            crawls=3,
            samples=60,
            low_confidence=False,
        ),
        health=[
            EngineHealth(
                engine=engine,
                verdict="present",
                cited_rate=0.5,
                mention_rate=0.5,
                samples=60,
                crawls=3,
                volatility=0.1,
                prompts=1,
            )
            for engine in engines
        ],
        changes=changes or [],
        actions=actions or [],
    )


def change(kind: str, *, prompt_id="p1", after="0%", engine=Engine.CHATGPT_SEARCH):
    """One movement between windows."""
    return InsightChange(
        kind=kind,
        prompt_id=prompt_id,
        prompt_text="What is the best procurement software?",
        engine=engine,
        before="40%",
        after=after,
        text=f"{kind} happened",
    )


def test_a_real_drop_fires_and_says_it_is_not_noise():
    """60 samples at 80% down to 60 samples at 20%: the bands are far apart."""
    events = evaluate(
        view(),
        [position(Engine.CHATGPT_SEARCH, samples=60, cited=12)],
        [position(Engine.CHATGPT_SEARCH, samples=60, cited=48)],
        rules={AlertRule.CITATION_DROP},
    )
    assert [e.rule for e in events] == [AlertRule.CITATION_DROP]
    assert "fell 60 points" in events[0].title
    assert "do not overlap" in events[0].detail
    assert events[0].numbers["samples"] == 60


def test_a_wobble_inside_the_bands_stays_silent():
    """55% to 45% on 60 samples is sampling, not a story."""
    events = evaluate(
        view(),
        [position(Engine.CHATGPT_SEARCH, samples=60, cited=27)],
        [position(Engine.CHATGPT_SEARCH, samples=60, cited=33)],
        rules={AlertRule.CITATION_DROP},
    )
    assert events == []


def test_a_tiny_window_never_fires_the_drop_rule():
    """Three samples can go 100% to 0% and mean nothing at all."""
    small = MIN_TRIALS - 1
    events = evaluate(
        view(),
        [position(Engine.CHATGPT_SEARCH, samples=small, cited=0)],
        [position(Engine.CHATGPT_SEARCH, samples=small, cited=small)],
        rules={AlertRule.CITATION_DROP},
    )
    assert events == []


def test_falling_to_zero_is_critical_rather_than_a_warning():
    """Disappearing from a platform outranks merely sliding on it."""
    events = evaluate(
        view(),
        [position(Engine.CHATGPT_SEARCH, samples=60, cited=0)],
        [position(Engine.CHATGPT_SEARCH, samples=60, cited=45)],
        rules={AlertRule.CITATION_DROP},
    )
    assert events[0].severity is AlertSeverity.CRITICAL


def test_only_starred_prompts_raise_the_lost_prompt_rule():
    """Every project loses prompts; the analyst says which ones matter."""
    insights = view(
        changes=[change("flip_down", prompt_id="p1"), change("flip_down", prompt_id="p2")]
    )
    events = evaluate(insights, [], [], rules={AlertRule.LOST_PROMPT}, important_prompt_ids={"p2"})
    assert [e.subject for e in events] == ["p2"]


def test_a_competitor_needs_several_prompts_to_count_as_a_surge():
    """One new citation is a data point; three is a trend."""
    once = view(changes=[change("new_competitor", after="coupa.com")])
    assert evaluate(once, [], [], rules={AlertRule.COMPETITOR_SURGE}) == []

    twice = view(
        changes=[
            change("new_competitor", after="coupa.com", prompt_id="p1"),
            change("new_competitor", after="coupa.com", prompt_id="p2"),
        ]
    )
    events = evaluate(twice, [], [], rules={AlertRule.COMPETITOR_SURGE})
    assert [e.subject for e in events] == ["coupa.com"]
    assert events[0].numbers["prompts"] == 2


def test_a_sourced_negative_claim_carries_its_quote_and_url():
    """The alert has to be actionable in the message itself."""
    card = ActionCard(
        id="a1",
        type="negative_claim",
        title="ChatGPT Search describes the brand negatively in 'pricing'",
        prescription="Correct the record on the page these answers cite.",
        impact_score=9.0,
        engine=Engine.CHATGPT_SEARCH,
        subtopic="pricing",
        metric="cited_rate",
        evidence=ActionEvidence(
            quotes=[
                EvidenceQuote(
                    text="Implementation timelines run long.",
                    engine=Engine.CHATGPT_SEARCH,
                    url="https://www.g2.com/products/gep-smart/reviews",
                )
            ]
        ),
    )
    events = evaluate(view(actions=[card]), [], [], rules={AlertRule.NEGATIVE_CLAIM})
    assert events[0].severity is AlertSeverity.CRITICAL
    assert "Implementation timelines run long." in events[0].detail
    assert events[0].url == "https://www.g2.com/products/gep-smart/reviews"

    card.status = "done"
    assert evaluate(view(actions=[card]), [], [], rules={AlertRule.NEGATIVE_CLAIM}) == []


def test_an_engine_that_stops_answering_is_caught():
    """This is the rule that finds a billing-blocked Gemini before the client does."""
    events = evaluate(
        view(engines=(Engine.GEMINI,)),
        [],
        [position(Engine.GEMINI, samples=60, cited=30)],
        rules={AlertRule.ENGINE_SILENT},
    )
    assert [e.rule for e in events] == [AlertRule.ENGINE_SILENT]
    assert "returned no answers" in events[0].title
    assert events[0].numbers["previous_samples"] == 60


def test_a_new_engine_with_no_history_is_not_reported_as_silent():
    """A platform added this window has nothing to have lost."""
    events = evaluate(view(engines=(Engine.GEMINI,)), [], [], rules={AlertRule.ENGINE_SILENT})
    assert events == []


def test_rules_that_are_off_are_never_evaluated():
    """The destination's rule list is the first gate, not a post-filter."""
    insights = view(changes=[change("flip_down", prompt_id="p1")])
    assert evaluate(insights, [], [], rules=set(), important_prompt_ids={"p1"}) == []


def test_events_come_back_most_severe_first():
    """A Slack message nobody scrolls has to lead with the worst news."""
    insights = view(
        changes=[change("flip_down", prompt_id="p1")],
        engines=(Engine.CHATGPT_SEARCH, Engine.GEMINI),
    )
    events = evaluate(
        insights,
        [position(Engine.CHATGPT_SEARCH, samples=60, cited=0)],
        [
            position(Engine.CHATGPT_SEARCH, samples=60, cited=45),
            position(Engine.GEMINI, samples=60, cited=20),
        ],
        rules=ALL_RULES,
        important_prompt_ids={"p1"},
    )
    assert events[0].severity is AlertSeverity.CRITICAL
    assert [e.severity for e in events] == sorted(
        [e.severity for e in events],
        key=lambda s: {"critical": 0, "warning": 1, "info": 2}[s.value],
    )


def test_engine_totals_ignore_failed_samples():
    """A sample that errored is not an answer that failed to cite."""
    rows = [
        ConsolidatedPosition(
            prompt_id="p1",
            engine=Engine.PERPLEXITY,
            runs=1,
            first_run_at=NOW,
            last_run_at=NOW,
            samples=30,
            failed_samples=10,
            cited_samples=5,
            citation_rate=0.25,
            cited=True,
            mention_samples=5,
            mention_rate=0.25,
            mentioned=True,
        )
    ]
    assert engine_totals(rows) == {Engine.PERPLEXITY: (5, 20)}


def test_the_dedupe_key_is_the_rule_the_platform_and_the_subject():
    """Same three things, same alert, whatever the wording."""
    events = evaluate(
        view(),
        [position(Engine.CHATGPT_SEARCH, samples=60, cited=0)],
        [position(Engine.CHATGPT_SEARCH, samples=60, cited=45)],
        rules={AlertRule.CITATION_DROP},
    )
    assert events[0].dedupe_key == "citation_drop|CHATGPT_SEARCH|CHATGPT_SEARCH"
