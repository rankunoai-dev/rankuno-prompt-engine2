"""Three-layer intent and entity gate.

Semrush returns every question containing the seed phrase — job hunts, stock
tickers and "top 10 companies" listicles included. Off-the-shelf tools pass
that noise straight into prompt research. This gate removes it deterministically:

* **Layer 1 — rejection patterns.** Hard noise (jobs, salaries, tickers, company
  listicles, courses). Any hit rejects with a score of 0.1.
* **Layer 2 — product-intent score.** Software/solution vocabulary and question
  framing raise a score in [0, 1].
* **Layer 3 — gate.** Score at or above the threshold keeps the prompt.

Alongside the gate the module classifies search intent, buyer stage and whether
the prompt is branded — all rule-based and inspectable. An LLM judge for
borderline scores is a documented gap, not a hidden dependency.
"""

from __future__ import annotations

import re
from typing import Final

from src.modules.prompt_tracking.schemas import (
    ClientProfile,
    DecisionStage,
    IntentAction,
    IntentDecision,
    PromptType,
    SearchIntent,
)

__all__ = ["IntentClassifier", "KEEP_THRESHOLD"]

KEEP_THRESHOLD: Final = 0.6

REJECT_PATTERNS: Final[tuple[str, ...]] = (
    r"\b(top|best|leading|largest)\s+\d+\s+(companies|vendors|providers|firms)\b",
    r"\b(companies|vendors|providers|firms)\s+(list|ranking|in\s+\w+)\b",
    r"\bjobs?\b",
    r"\bsalar(y|ies)\b",
    r"\bcareers?\b",
    r"\bhiring\b",
    r"\binternships?\b",
    r"\bstocks?\b",
    r"\bshares?\b",
    r"\bshare price\b",
    r"\bticker\b",
    r"\bcourses?\b",
    r"\bcertifications?\b",
    r"\bdegree\b",
    r"\bnews\b",
)

SOFTWARE_PATTERNS: Final[tuple[str, ...]] = (
    r"\bsoftware\b",
    r"\bplatforms?\b",
    r"\bsystems?\b",
    r"\btools?\b",
    r"\bsolutions?\b",
    r"\bapplications?\b",
    r"\bfeatures?\b",
    r"\bpricing\b",
    r"\bcost\b",
    r"\bintegrations?\b",
    r"\bautomation\b",
    r"\bmodules?\b",
    r"\bvendors?\b",
    r"\berp\b",
    r"\bwms\b",
    r"\btms\b",
    r"\bsaas\b",
    r"\bcloud\b",
    r"\bai\b",
)

QUESTION_PATTERN: Final = re.compile(
    r"^\s*(what|how|which|why|is|are|does|do|can|should|when|where|who)\b", re.I
)

_TRANSACTIONAL: Final = re.compile(
    r"\b(pric(e|ing)|cost|quote|buy|purchase|demo|free trial|subscription|license|licence)\b", re.I
)
_COMMERCIAL: Final = re.compile(
    r"\b(best|top|vs\.?|versus|compar(e|ison)|alternatives?|reviews?|worth|better than|"
    r"leading|recommend(ed)?|choose|options?)\b",
    re.I,
)
_POST_PURCHASE: Final = re.compile(
    r"\b(how to use|login|log in|support|troubleshoot|implement(ation)?|onboard(ing)?|"
    r"integrate with|migrat(e|ion)|renew(al)?|cancel|customer service|training|set ?up|"
    r"configur(e|ation)|after (buying|purchase))\b",
    re.I,
)
_NAVIGATIONAL: Final = re.compile(r"\b(website|official site|homepage|contact|address)\b", re.I)


class IntentClassifier:
    """Scores prompts for software-solution intent and classifies them."""

    def __init__(
        self,
        *,
        reject_patterns: tuple[str, ...] = REJECT_PATTERNS,
        software_patterns: tuple[str, ...] = SOFTWARE_PATTERNS,
        threshold: float = KEEP_THRESHOLD,
    ) -> None:
        """Compile the pattern sets once.

        Args:
            reject_patterns: Layer 1 noise patterns.
            software_patterns: Layer 2 positive signals.
            threshold: Layer 3 minimum score to keep.
        """
        self._reject = [re.compile(p, re.I) for p in reject_patterns]
        self._software = [re.compile(p, re.I) for p in software_patterns]
        self._threshold = threshold

    def decide(self, prompt: str) -> IntentDecision:
        """Run all three layers on `prompt`."""
        text = prompt.strip()
        for pattern in self._reject:
            if pattern.search(text):
                return IntentDecision(
                    prompt_text=text,
                    action=IntentAction.REJECT,
                    score=0.1,
                    entity_type="Company / Job / Investor noise",
                    reason="Layer 1 rejection pattern matched.",
                    matched_pattern=pattern.pattern,
                )

        matches = sum(1 for pattern in self._software if pattern.search(text))
        score = 0.0
        if matches:
            score = min(1.0, 0.6 + 0.15 * (matches - 1))
        if QUESTION_PATTERN.search(text):
            score = min(1.0, score + (0.1 if matches else 0.45))

        if score >= self._threshold:
            return IntentDecision(
                prompt_text=text,
                action=IntentAction.KEEP,
                score=round(score, 3),
                entity_type="Software product / solution",
                reason=f"Layer 2 matched {matches} product-intent signal(s).",
            )
        return IntentDecision(
            prompt_text=text,
            action=IntentAction.REJECT,
            score=round(score, 3),
            entity_type="Generic query",
            reason="Layer 3 gate: insufficient software-intent specificity.",
        )

    @staticmethod
    def search_intent(prompt: str) -> SearchIntent:
        """Classify search intent from vocabulary; transactional wins ties."""
        if _TRANSACTIONAL.search(prompt):
            return SearchIntent.TRANSACTIONAL
        if _COMMERCIAL.search(prompt):
            return SearchIntent.COMMERCIAL
        if _NAVIGATIONAL.search(prompt):
            return SearchIntent.NAVIGATIONAL
        return SearchIntent.INFORMATIONAL

    @staticmethod
    def decision_stage(prompt: str, intent: SearchIntent) -> DecisionStage:
        """Map a prompt to the buyer-journey stage."""
        if _POST_PURCHASE.search(prompt):
            return DecisionStage.POST_PURCHASE
        if intent is SearchIntent.TRANSACTIONAL:
            return DecisionStage.DECISION
        if intent in (SearchIntent.COMMERCIAL, SearchIntent.NAVIGATIONAL):
            return DecisionStage.CONSIDERATION
        return DecisionStage.AWARENESS

    @staticmethod
    def prompt_type(prompt: str, client: ClientProfile) -> PromptType:
        """Branded if the brand name or any alias appears on a word boundary."""
        for term in client.brand_terms():
            if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", prompt, re.I):
                return PromptType.BRANDED
        return PromptType.NON_BRANDED
