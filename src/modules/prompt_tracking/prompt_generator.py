"""Turns Semrush keyword demand into candidate prompts.

Non-branded candidates are Semrush's own question phrases: real queries with
real volume. Branded candidates rarely exist in keyword data for B2B brands, so
they are generated from stage-specific templates around each seed keyword, with
the seed's own volume as the demand proxy. Every candidate is scored by the
intent gate before it can be selected.
"""

from __future__ import annotations

from typing import Final

from src.integrations.schemas import KeywordRecord
from src.modules.prompt_tracking.intent_filter import IntentClassifier
from src.modules.prompt_tracking.schemas import (
    ClientProfile,
    CustomPrompt,
    DecisionStage,
    IntentAction,
    IntentDecision,
    PromptCandidate,
    PromptType,
)

__all__ = ["PromptGenerator"]

BRANDED_TEMPLATES: Final[dict[DecisionStage, tuple[str, ...]]] = {
    DecisionStage.AWARENESS: (
        "What does {brand} offer for {keyword}?",
        "How does {brand} approach {keyword}?",
    ),
    DecisionStage.CONSIDERATION: (
        "How does {brand} compare to other {keyword} vendors?",
        "Is {brand} a good choice for {keyword}?",
        "What are the key features of {brand} {keyword}?",
    ),
    DecisionStage.DECISION: (
        "What is {brand} pricing for {keyword}?",
        "How much does {brand} {keyword} cost for an enterprise?",
    ),
    DecisionStage.POST_PURCHASE: (
        "How do I implement {brand} {keyword} and integrate it with my ERP?",
        "What training and support does {brand} provide for {keyword}?",
    ),
}

NON_BRANDED_TEMPLATES: Final[dict[DecisionStage, tuple[str, ...]]] = {
    DecisionStage.AWARENESS: ("What is {keyword} software and how does it work?",),
    DecisionStage.CONSIDERATION: ("What are the best {keyword} platforms for enterprises?",),
    DecisionStage.DECISION: ("How much does {keyword} software cost?",),
    DecisionStage.POST_PURCHASE: (
        "How do I implement {keyword} software and integrate it with existing systems?",
    ),
}


class PromptGenerator:
    """Builds and scores prompt candidates for one client."""

    def __init__(self, classifier: IntentClassifier | None = None) -> None:
        """Use the default rule-based classifier unless one is injected."""
        self._classifier = classifier or IntentClassifier()

    def from_keywords(
        self,
        client: ClientProfile,
        seed_volumes: dict[str, int],
        questions: dict[str, list[KeywordRecord]],
    ) -> list[PromptCandidate]:
        """Generate every candidate, scored, de-duplicated by text.

        Args:
            client: Brand, domains and seed keywords.
            seed_volumes: Semrush volume per seed keyword (0 if unknown).
            questions: Semrush question rows per seed keyword.
        """
        candidates: dict[str, PromptCandidate] = {}

        for seed in client.seed_keywords:
            subtopic = self._subtopic_for(seed, client)
            volume = seed_volumes.get(seed, 0)

            for row in questions.get(seed, []):
                self._add(candidates, client, row.keyword, seed, row.search_volume, subtopic, row)

            for stage, templates in NON_BRANDED_TEMPLATES.items():
                for template in templates:
                    text = template.format(keyword=seed)
                    self._add(candidates, client, text, seed, volume, subtopic, None, stage)

            for stage, templates in BRANDED_TEMPLATES.items():
                for template in templates:
                    text = template.format(brand=client.brand_name, keyword=seed)
                    self._add(candidates, client, text, seed, volume, subtopic, None, stage)

        return list(candidates.values())

    def from_custom(
        self,
        client: ClientProfile,
        prompts: list[CustomPrompt],
        seed_volumes: dict[str, int],
    ) -> list[PromptCandidate]:
        """Wrap analyst-supplied prompts as candidates.

        The intent gate still runs so its verdict is visible, but it is advisory:
        an analyst who typed the prompt has already decided to track it.

        The text is sent to the engines exactly as supplied (whitespace
        collapsed by `CustomPrompt`); no casing or punctuation is changed, so
        the query the engines see is the query the analyst wrote.
        """
        candidates: dict[str, PromptCandidate] = {}
        for custom in prompts:
            prompt = custom.prompt_text
            key = prompt.lower()
            if key in candidates:
                continue
            seed = custom.keyword or self._closest_seed(prompt, client)
            gate = self._classifier.decide(prompt)
            decision = IntentDecision(
                prompt_text=prompt,
                action=IntentAction.KEEP,
                score=gate.score,
                entity_type=gate.entity_type,
                reason=f"Analyst-supplied (gate advisory: {gate.action.value}, {gate.reason})",
                matched_pattern=gate.matched_pattern,
            )
            intent = self._classifier.search_intent(prompt)
            candidates[key] = PromptCandidate(
                prompt_text=prompt,
                core_keyword=seed,
                search_volume=seed_volumes.get(seed, 0),
                subtopic=custom.subtopic or self._subtopic_for(seed, client),
                prompt_type=self._classifier.prompt_type(prompt, client),
                search_intent=intent,
                decision_stage=self._classifier.decision_stage(prompt, intent),
                keyword_source=None,
                intent=decision,
            )
        return list(candidates.values())

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _closest_seed(prompt: str, client: ClientProfile) -> str:
        """Seed keyword sharing the most words with `prompt`; the first seed otherwise."""
        tokens = set(prompt.lower().split())
        best = max(client.seed_keywords, key=lambda s: len(tokens & set(s.lower().split())))
        return best if tokens & set(best.lower().split()) else client.seed_keywords[0]

    def _add(
        self,
        into: dict[str, PromptCandidate],
        client: ClientProfile,
        text: str,
        seed: str,
        volume: int,
        subtopic: str,
        row: KeywordRecord | None,
        forced_stage: DecisionStage | None = None,
    ) -> None:
        """Score one prompt and add it unless a duplicate is already present."""
        prompt = _as_question(text)
        key = prompt.lower()
        if key in into:
            return
        decision = self._classifier.decide(prompt)
        intent = self._classifier.search_intent(prompt)
        stage = forced_stage or self._classifier.decision_stage(prompt, intent)
        prompt_type = self._classifier.prompt_type(prompt, client)
        if row is None and prompt_type is PromptType.NON_BRANDED and client.brand_name in prompt:
            prompt_type = PromptType.BRANDED  # pragma: no cover - defensive
        into[key] = PromptCandidate(
            prompt_text=prompt,
            core_keyword=seed,
            search_volume=volume,
            subtopic=subtopic,
            prompt_type=prompt_type,
            search_intent=intent,
            decision_stage=stage,
            keyword_source=row.source if row else None,
            intent=decision,
        )

    @staticmethod
    def _subtopic_for(seed: str, client: ClientProfile) -> str:
        """Pick the analyst subtopic sharing the most words with `seed`."""
        if not client.subtopics:
            return seed.title()
        seed_tokens = set(seed.lower().split())
        best = max(
            client.subtopics,
            key=lambda s: len(seed_tokens & set(s.lower().split())),
        )
        return best if seed_tokens & set(best.lower().split()) else client.subtopics[0]


def _as_question(text: str) -> str:
    """Normalise casing and terminal punctuation of a prompt."""
    cleaned = " ".join(text.strip().split())
    if not cleaned:
        return cleaned
    cleaned = cleaned[0].upper() + cleaned[1:]
    if cleaned[-1] not in "?.!":
        cleaned += "?"
    return cleaned


def kept(candidates: list[PromptCandidate]) -> list[PromptCandidate]:
    """Candidates that passed the intent gate."""
    return [c for c in candidates if c.intent.action is IntentAction.KEEP]
