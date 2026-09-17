"""Selects the 10 branded + 10 non-branded master prompt set.

The blueprint requires exactly twenty prompts per LOB, balanced across the four
buyer-journey stages. Selection is a round-robin over stages ordered by a
quality key (intent score, then search volume), so no stage is starved by a
high-volume neighbour, and any shortfall is reported rather than padded with
rejected prompts.
"""

from __future__ import annotations

from collections import defaultdict

from src.modules.prompt_tracking.schemas import (
    DecisionStage,
    IntentAction,
    PromptCandidate,
    PromptType,
)

__all__ = ["PER_TYPE_QUOTA", "SelectionResult", "select_master_set"]

PER_TYPE_QUOTA = 10


class SelectionResult:
    """Selected prompts plus anything the caller should warn about."""

    def __init__(self, selected: list[PromptCandidate], warnings: list[str]) -> None:
        """Store the selection."""
        self.selected = selected
        self.warnings = warnings


def select_master_set(
    candidates: list[PromptCandidate], *, per_type: int = PER_TYPE_QUOTA
) -> SelectionResult:
    """Pick `per_type` branded and `per_type` non-branded prompts, stage-balanced."""
    kept = [c for c in candidates if c.intent.action is IntentAction.KEEP]
    selected: list[PromptCandidate] = []
    warnings: list[str] = []

    for prompt_type in (PromptType.BRANDED, PromptType.NON_BRANDED):
        pool = [c for c in kept if c.prompt_type is prompt_type]
        chosen = _balanced_pick(pool, per_type)
        if len(chosen) < per_type:
            warnings.append(
                f"Only {len(chosen)} {prompt_type.value.lower().replace('_', '-')} prompts "
                f"passed the intent gate; {per_type} required. Add seed keywords or subtopics."
            )
        selected.extend(chosen)

    return SelectionResult(selected, warnings)


def _balanced_pick(pool: list[PromptCandidate], quota: int) -> list[PromptCandidate]:
    """Round-robin across stages by quality; fill remaining slots by quality."""
    by_stage: dict[DecisionStage, list[PromptCandidate]] = defaultdict(list)
    for candidate in pool:
        by_stage[candidate.decision_stage].append(candidate)
    for bucket in by_stage.values():
        bucket.sort(key=_quality, reverse=True)

    chosen: list[PromptCandidate] = []
    stages = list(DecisionStage)
    while len(chosen) < quota and any(by_stage[s] for s in stages):
        for stage in stages:
            if by_stage[stage] and len(chosen) < quota:
                chosen.append(by_stage[stage].pop(0))
    return chosen


def _quality(candidate: PromptCandidate) -> tuple[float, int, bool]:
    """Higher is better: intent score, then volume, then real-keyword origin."""
    return (candidate.intent.score, candidate.search_volume, candidate.keyword_source is not None)
