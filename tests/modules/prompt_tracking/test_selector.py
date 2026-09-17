"""Tests for stage-balanced selection of the master prompt set."""

from __future__ import annotations

import itertools

import pytest

from src.integrations.schemas import KeywordSource
from src.modules.prompt_tracking.schemas import (
    DecisionStage,
    IntentAction,
    IntentDecision,
    PromptCandidate,
    PromptType,
    SearchIntent,
)
from src.modules.prompt_tracking.selector import PER_TYPE_QUOTA, select_master_set

_counter = itertools.count()


def _candidate(
    prompt_type: PromptType,
    stage: DecisionStage,
    *,
    score: float = 0.7,
    volume: int = 100,
    action: IntentAction = IntentAction.KEEP,
    source: KeywordSource | None = None,
    text: str | None = None,
) -> PromptCandidate:
    text = text or f"Prompt {next(_counter)} {prompt_type.value} {stage.value}?"
    return PromptCandidate(
        prompt_text=text,
        core_keyword="procurement software",
        search_volume=volume,
        subtopic="Procurement Software",
        prompt_type=prompt_type,
        search_intent=SearchIntent.INFORMATIONAL,
        decision_stage=stage,
        keyword_source=source,
        intent=IntentDecision(
            prompt_text=text,
            action=action,
            score=score,
            entity_type="Software product / solution",
            reason="test",
        ),
    )


def _full_pool(per_stage: int = 3) -> list[PromptCandidate]:
    return [
        _candidate(prompt_type, stage)
        for prompt_type in PromptType
        for stage in DecisionStage
        for _ in range(per_stage)
    ]


class TestQuota:
    def test_exactly_ten_per_type_when_enough_exist(self):
        result = select_master_set(_full_pool())
        assert len(result.selected) == 2 * PER_TYPE_QUOTA
        branded = [c for c in result.selected if c.prompt_type is PromptType.BRANDED]
        assert len(branded) == PER_TYPE_QUOTA
        assert len(result.selected) - len(branded) == PER_TYPE_QUOTA
        assert result.warnings == []

    def test_branded_are_listed_before_non_branded(self):
        result = select_master_set(_full_pool())
        types = [c.prompt_type for c in result.selected]
        assert types[:PER_TYPE_QUOTA] == [PromptType.BRANDED] * PER_TYPE_QUOTA
        assert types[PER_TYPE_QUOTA:] == [PromptType.NON_BRANDED] * PER_TYPE_QUOTA

    def test_per_type_override(self):
        result = select_master_set(_full_pool(), per_type=2)
        assert len(result.selected) == 4
        assert result.warnings == []

    def test_selected_texts_are_unique(self):
        result = select_master_set(_full_pool())
        assert len({c.prompt_text for c in result.selected}) == len(result.selected)


class TestRoundRobin:
    def test_first_four_picks_cover_four_distinct_stages(self):
        result = select_master_set(_full_pool(per_stage=3))
        for offset in (0, PER_TYPE_QUOTA):
            first_four = result.selected[offset : offset + 4]
            assert {c.decision_stage for c in first_four} == set(DecisionStage)
            next_four = result.selected[offset + 4 : offset + 8]
            assert {c.decision_stage for c in next_four} == set(DecisionStage)

    def test_remaining_slots_fill_from_stages_that_still_have_candidates(self):
        pool = [_candidate(PromptType.BRANDED, DecisionStage.AWARENESS) for _ in range(6)]
        pool += [_candidate(PromptType.BRANDED, DecisionStage.DECISION) for _ in range(2)]
        result = select_master_set(pool, per_type=6)
        stages = [c.decision_stage for c in result.selected]
        assert len(stages) == 6
        assert stages.count(DecisionStage.DECISION) == 2
        assert stages.count(DecisionStage.AWARENESS) == 4


class TestQualityOrdering:
    def test_higher_intent_score_wins_within_a_stage(self):
        low = _candidate(PromptType.BRANDED, DecisionStage.AWARENESS, score=0.7, volume=9999)
        high = _candidate(PromptType.BRANDED, DecisionStage.AWARENESS, score=0.9, volume=1)
        result = select_master_set([low, high], per_type=1)
        assert result.selected == [high]

    def test_volume_breaks_score_ties(self):
        small = _candidate(PromptType.NON_BRANDED, DecisionStage.DECISION, score=0.8, volume=10)
        big = _candidate(PromptType.NON_BRANDED, DecisionStage.DECISION, score=0.8, volume=500)
        result = select_master_set([small, big], per_type=1)
        assert result.selected == [big]

    def test_real_keyword_origin_breaks_remaining_ties(self):
        templated = _candidate(PromptType.NON_BRANDED, DecisionStage.DECISION)
        harvested = _candidate(
            PromptType.NON_BRANDED, DecisionStage.DECISION, source=KeywordSource.PHRASE_QUESTIONS
        )
        result = select_master_set([templated, harvested], per_type=1)
        assert result.selected == [harvested]


class TestShortfall:
    def test_warning_names_type_and_count(self):
        pool = [_candidate(PromptType.BRANDED, DecisionStage.AWARENESS) for _ in range(3)]
        pool += _full_pool()[len(list(DecisionStage)) * 3 :]  # only the non-branded half
        result = select_master_set(pool)
        assert len(result.warnings) == 1
        assert "Only 3 branded prompts" in result.warnings[0]
        assert f"{PER_TYPE_QUOTA} required" in result.warnings[0]

    def test_non_branded_shortfall_is_hyphenated(self):
        pool = [_candidate(PromptType.NON_BRANDED, DecisionStage.DECISION)]
        result = select_master_set(pool, per_type=2)
        assert any("Only 1 non-branded prompts" in w for w in result.warnings)
        assert any("Only 0 branded prompts" in w for w in result.warnings)

    def test_empty_pool_selects_nothing_and_warns_twice(self):
        result = select_master_set([])
        assert result.selected == []
        assert len(result.warnings) == 2


class TestRejectedCandidates:
    def test_rejected_candidates_are_never_selected(self):
        rejected = [
            _candidate(PromptType.BRANDED, stage, score=0.1, action=IntentAction.REJECT)
            for stage in DecisionStage
        ]
        kept_one = _candidate(PromptType.BRANDED, DecisionStage.AWARENESS, score=0.6)
        result = select_master_set([*rejected, kept_one], per_type=3)
        assert result.selected == [kept_one]
        assert all(c.intent.action is IntentAction.KEEP for c in result.selected)


@pytest.mark.parametrize("per_type", [1, 5, 10])
def test_never_exceeds_quota(per_type):
    result = select_master_set(_full_pool(per_stage=5), per_type=per_type)
    assert len(result.selected) == 2 * per_type
