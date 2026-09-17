"""Tests for landing-page mapping and content-gap detection."""

from __future__ import annotations

import pytest

from src.modules.prompt_tracking.schemas import (
    DecisionStage,
    IntentAction,
    IntentDecision,
    PromptCandidate,
    PromptType,
    SearchIntent,
)
from src.modules.prompt_tracking.url_mapper import UrlMapper, content_gap_label

PROCUREMENT_URL = "https://www.gep.com/software/procurement-software"
SUPPLY_CHAIN_URL = "https://www.gep.com/software/supply-chain-software"
ABOUT_URL = "https://www.gep.com/about-us"
INVENTORY = [PROCUREMENT_URL, SUPPLY_CHAIN_URL, ABOUT_URL]


def _candidate(text: str, keyword: str, subtopic: str) -> PromptCandidate:
    return PromptCandidate(
        prompt_text=text,
        core_keyword=keyword,
        search_volume=10,
        subtopic=subtopic,
        prompt_type=PromptType.NON_BRANDED,
        search_intent=SearchIntent.INFORMATIONAL,
        decision_stage=DecisionStage.AWARENESS,
        intent=IntentDecision(
            prompt_text=text,
            action=IntentAction.KEEP,
            score=0.7,
            entity_type="Software product / solution",
            reason="test",
        ),
    )


@pytest.fixture
def mapper() -> UrlMapper:
    return UrlMapper(INVENTORY)


class TestMapping:
    def test_procurement_candidate_maps_to_procurement_url(self, mapper):
        candidate = _candidate(
            "What is the best procurement software for enterprises?",
            "procurement software",
            "Procurement Software",
        )
        result = mapper.map(candidate)
        assert result.mapped_url == PROCUREMENT_URL
        assert result.content_gap is False
        assert result.label == PROCUREMENT_URL
        assert result.score > 0.34

    def test_supply_chain_candidate_maps_to_supply_chain_url(self, mapper):
        candidate = _candidate(
            "How does supply chain software work?", "supply chain software", "Supply Chain"
        )
        assert mapper.map(candidate).mapped_url == SUPPLY_CHAIN_URL

    def test_unrelated_topic_is_a_content_gap(self, mapper):
        candidate = _candidate(
            "How does contract lifecycle management work?",
            "contract lifecycle management",
            "contract lifecycle management",
        )
        result = mapper.map(candidate)
        assert result.mapped_url is None
        assert result.content_gap is True
        assert result.score == pytest.approx(0.0)
        assert result.label == "[CONTENT GAP: Need Contract Lifecycle Management Page]"

    def test_empty_inventory_is_always_a_gap(self):
        candidate = _candidate(
            "What is procurement software?", "procurement software", "Procurement Software"
        )
        result = UrlMapper([]).map(candidate)
        assert result.content_gap is True
        assert result.mapped_url is None
        assert result.label == "[CONTENT GAP: Need Procurement Software Page]"

    def test_urls_without_path_tokens_are_ignored(self):
        candidate = _candidate(
            "What is procurement software?", "procurement software", "Procurement Software"
        )
        result = UrlMapper(["https://www.gep.com/", "https://www.gep.com/index.html"]).map(
            candidate
        )
        assert result.content_gap is True

    def test_bare_host_urls_are_indexed(self):
        candidate = _candidate(
            "What is procurement software?", "procurement software", "Procurement Software"
        )
        result = UrlMapper(["www.gep.com/software/procurement-software"]).map(candidate)
        assert result.mapped_url == "www.gep.com/software/procurement-software"


class TestThreshold:
    @pytest.fixture
    def partial_overlap(self) -> PromptCandidate:
        # Shares only "software" with the procurement slug: score 1/2 = 0.5.
        return _candidate(
            "What is spend management software?",
            "spend management software",
            "Spend Management",
        )

    def test_default_threshold_accepts_partial_overlap(self, mapper, partial_overlap):
        result = mapper.map(partial_overlap)
        assert result.mapped_url == PROCUREMENT_URL
        assert result.score == pytest.approx(0.5)

    def test_higher_threshold_turns_it_into_a_gap(self, partial_overlap):
        result = UrlMapper(INVENTORY, threshold=0.6).map(partial_overlap)
        assert result.content_gap is True
        assert result.mapped_url is None
        assert result.score == pytest.approx(0.5)
        assert result.label == "[CONTENT GAP: Need Spend Management Page]"


class TestContentGapLabel:
    def test_title_cases_and_strips(self):
        assert content_gap_label("  procurement software ") == (
            "[CONTENT GAP: Need Procurement Software Page]"
        )

    def test_already_titled_is_unchanged(self):
        assert content_gap_label("Supply Chain") == "[CONTENT GAP: Need Supply Chain Page]"
