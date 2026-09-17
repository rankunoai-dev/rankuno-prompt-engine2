"""Tests for the three-layer intent gate and rule-based classifiers."""

from __future__ import annotations

import pytest

from src.modules.prompt_tracking.intent_filter import KEEP_THRESHOLD, IntentClassifier
from src.modules.prompt_tracking.schemas import (
    ClientProfile,
    DecisionStage,
    IntentAction,
    PromptType,
    SearchIntent,
)


@pytest.fixture
def classifier() -> IntentClassifier:
    return IntentClassifier()


@pytest.fixture
def client() -> ClientProfile:
    return ClientProfile(
        brand_name="GEP",
        aliases=["GEP SMART", "NEXXE"],
        domains=["gep.com"],
        lob="Procurement Software",
        seed_keywords=["procurement software"],
    )


class TestLayerOneRejection:
    @pytest.mark.parametrize(
        "prompt",
        [
            "top 10 supply chain management companies in us",
            "supply chain manager salary in canada",
            "supply chain software companies stock price",
            "supply chain management jobs in new york",
            "best supply chain courses",
        ],
    )
    def test_noise_is_rejected_with_pattern(self, classifier, prompt):
        decision = classifier.decide(prompt)
        assert decision.action is IntentAction.REJECT
        assert decision.score == pytest.approx(0.1)
        assert decision.matched_pattern is not None
        assert "Layer 1" in decision.reason

    def test_prompt_text_is_stripped(self, classifier):
        decision = classifier.decide("  supply chain jobs  ")
        assert decision.prompt_text == "supply chain jobs"


class TestLayerTwoAndThree:
    @pytest.mark.parametrize(
        ("prompt", "expected_score"),
        [
            ("what is supply chain management software", 0.7),
            ("how much does supply chain software cost", 0.85),
            ("best WMS supply chain software for small business", 0.75),
            ("how does ERP supply chain software integrate with SAP", 0.85),
        ],
    )
    def test_software_intent_is_kept(self, classifier, prompt, expected_score):
        decision = classifier.decide(prompt)
        assert decision.action is IntentAction.KEEP
        assert decision.score == pytest.approx(expected_score)
        assert decision.matched_pattern is None
        assert decision.score >= KEEP_THRESHOLD

    def test_generic_phrase_is_rejected(self, classifier):
        decision = classifier.decide("supply chain")
        assert decision.action is IntentAction.REJECT
        assert decision.score == pytest.approx(0.0)
        assert "Layer 3" in decision.reason

    def test_bare_question_without_software_signal_is_rejected(self, classifier):
        decision = classifier.decide("what is supply chain")
        assert decision.action is IntentAction.REJECT
        assert decision.score == pytest.approx(0.45)
        assert decision.score < KEEP_THRESHOLD

    def test_score_caps_at_one(self, classifier):
        decision = classifier.decide(
            "what software platform system tool solution automation module is best"
        )
        assert decision.action is IntentAction.KEEP
        assert decision.score == pytest.approx(1.0)


class TestSearchIntent:
    @pytest.mark.parametrize(
        ("prompt", "expected"),
        [
            ("procurement software pricing", SearchIntent.TRANSACTIONAL),
            ("how much does procurement software cost", SearchIntent.TRANSACTIONAL),
            ("best procurement software", SearchIntent.COMMERCIAL),
            ("gep vs coupa", SearchIntent.COMMERCIAL),
            ("compare procurement platforms", SearchIntent.COMMERCIAL),
            ("gep official website", SearchIntent.NAVIGATIONAL),
            ("what is procurement software", SearchIntent.INFORMATIONAL),
        ],
    )
    def test_vocabulary_drives_intent(self, prompt, expected):
        assert IntentClassifier.search_intent(prompt) is expected

    def test_transactional_wins_ties(self):
        assert IntentClassifier.search_intent("best pricing") is SearchIntent.TRANSACTIONAL


class TestDecisionStage:
    @pytest.mark.parametrize(
        "intent",
        list(SearchIntent),
    )
    def test_post_purchase_vocabulary_overrides_intent(self, intent):
        stage = IntentClassifier.decision_stage("how to use X login support", intent)
        assert stage is DecisionStage.POST_PURCHASE

    @pytest.mark.parametrize(
        ("intent", "expected"),
        [
            (SearchIntent.TRANSACTIONAL, DecisionStage.DECISION),
            (SearchIntent.COMMERCIAL, DecisionStage.CONSIDERATION),
            (SearchIntent.NAVIGATIONAL, DecisionStage.CONSIDERATION),
            (SearchIntent.INFORMATIONAL, DecisionStage.AWARENESS),
        ],
    )
    def test_intent_maps_to_stage(self, intent, expected):
        assert IntentClassifier.decision_stage("what is procurement software", intent) is expected


class TestPromptType:
    def test_alias_matches_case_insensitively(self, client):
        assert IntentClassifier.prompt_type("GEP SMART pricing", client) is PromptType.BRANDED
        assert IntentClassifier.prompt_type("gep smart pricing", client) is PromptType.BRANDED
        assert IntentClassifier.prompt_type("is nexxe any good", client) is PromptType.BRANDED

    def test_brand_name_matches_on_word_boundary(self, client):
        assert IntentClassifier.prompt_type("what does gep offer", client) is PromptType.BRANDED
        assert IntentClassifier.prompt_type("Is GEP good?", client) is PromptType.BRANDED

    def test_substring_inside_another_word_is_not_branded(self, client):
        result = IntentClassifier.prompt_type("how does geppetto software work", client)
        assert result is PromptType.NON_BRANDED

    def test_unrelated_prompt_is_non_branded(self, client):
        result = IntentClassifier.prompt_type("what is procurement software", client)
        assert result is PromptType.NON_BRANDED


class TestCustomConfiguration:
    def test_custom_reject_patterns_replace_defaults(self):
        custom = IntentClassifier(reject_patterns=(r"\bbanana\b",))
        rejected = custom.decide("banana software")
        assert rejected.action is IntentAction.REJECT
        assert rejected.matched_pattern == r"\bbanana\b"
        # "jobs" is no longer a rejection pattern under the custom set.
        assert custom.decide("what software jobs exist").action is IntentAction.KEEP

    def test_custom_software_patterns_and_threshold(self):
        strict = IntentClassifier(software_patterns=(r"\bwidget\b",), threshold=0.9)
        assert strict.decide("what is a widget").action is IntentAction.REJECT
        assert strict.decide("what is a widget").score == pytest.approx(0.7)
        # The default software vocabulary no longer counts.
        assert strict.decide("what is procurement software").score == pytest.approx(0.45)

        lenient = IntentClassifier(software_patterns=(r"\bwidget\b",), threshold=0.5)
        assert lenient.decide("widget pricing").action is IntentAction.KEEP
        assert lenient.decide("widget pricing").score == pytest.approx(0.6)
