"""Tests for candidate generation from Semrush questions and templates."""

from __future__ import annotations

import pytest

from src.integrations.schemas import KeywordRecord, KeywordSource
from src.modules.prompt_tracking.prompt_generator import (
    BRANDED_TEMPLATES,
    NON_BRANDED_TEMPLATES,
    PromptGenerator,
    _as_question,
    kept,
)
from src.modules.prompt_tracking.schemas import (
    ClientProfile,
    DecisionStage,
    IntentAction,
    PromptCandidate,
    PromptType,
)

SEED = "procurement software"
SEED_VOLUME = 5400


def _row(keyword: str, volume: int) -> KeywordRecord:
    return KeywordRecord(
        keyword=keyword,
        search_volume=volume,
        database="us",
        source=KeywordSource.PHRASE_QUESTIONS,
    )


def _client(subtopics: list[str] | None = None) -> ClientProfile:
    return ClientProfile(
        brand_name="GEP",
        aliases=["GEP SMART"],
        domains=["gep.com"],
        lob="Procurement Software",
        seed_keywords=[SEED],
        subtopics=subtopics or [],
    )


@pytest.fixture
def rows() -> list[KeywordRecord]:
    return [
        _row("what is procurement software", 880),
        _row("how does procurement software work", 320),
        _row("procurement software jobs", 210),
        _row("is gep smart good for procurement software", 40),
        # Same text as a branded DECISION template, differing only in case.
        _row("how much does gep procurement software cost for an enterprise", 30),
        _row("Which procurement software is best?", 90),
        _row("why buy procurement software.", 10),
    ]


@pytest.fixture
def candidates(rows) -> list[PromptCandidate]:
    return PromptGenerator().from_keywords(_client(), {SEED: SEED_VOLUME}, {SEED: rows})


def _by_text(candidates: list[PromptCandidate], text: str) -> PromptCandidate:
    """Look a candidate up by text; de-duplication is case-insensitive, so match that way."""
    matches = [c for c in candidates if c.prompt_text.lower() == text.lower()]
    assert len(matches) == 1, f"expected exactly one candidate for {text!r}"
    return matches[0]


class TestQuestionRows:
    def test_question_keeps_its_own_volume_and_source(self, candidates):
        candidate = _by_text(candidates, "What is procurement software?")
        assert candidate.search_volume == 880
        assert candidate.keyword_source is KeywordSource.PHRASE_QUESTIONS
        assert candidate.core_keyword == SEED
        assert candidate.prompt_type is PromptType.NON_BRANDED
        assert candidate.intent.action is IntentAction.KEEP

    def test_noise_rows_are_scored_but_rejected(self, candidates):
        candidate = _by_text(candidates, "Procurement software jobs?")
        assert candidate.intent.action is IntentAction.REJECT

    def test_branded_question_row_is_branded(self, candidates):
        candidate = _by_text(candidates, "Is gep smart good for procurement software?")
        assert candidate.prompt_type is PromptType.BRANDED
        assert candidate.keyword_source is KeywordSource.PHRASE_QUESTIONS


class TestTemplates:
    def test_template_candidates_use_seed_volume_and_no_source(self, candidates):
        templated = [c for c in candidates if c.keyword_source is None]
        assert templated
        assert all(c.search_volume == SEED_VOLUME for c in templated)
        assert all(c.core_keyword == SEED for c in templated)

    def test_branded_templates_produce_branded_candidates(self, candidates):
        for stage, templates in BRANDED_TEMPLATES.items():
            for template in templates:
                text = _as_question(template.format(brand="GEP", keyword=SEED))
                candidate = _by_text(candidates, text)
                assert candidate.prompt_type is PromptType.BRANDED
                assert candidate.decision_stage is stage

    def test_non_branded_templates_are_non_branded(self, candidates):
        for stage, templates in NON_BRANDED_TEMPLATES.items():
            for template in templates:
                candidate = _by_text(candidates, _as_question(template.format(keyword=SEED)))
                assert candidate.prompt_type is PromptType.NON_BRANDED
                assert candidate.decision_stage is stage

    def test_every_stage_appears_for_both_types(self, candidates):
        templated = [c for c in candidates if c.keyword_source is None]
        for prompt_type in PromptType:
            stages = {c.decision_stage for c in templated if c.prompt_type is prompt_type}
            assert stages == set(DecisionStage)


class TestNormalisation:
    def test_duplicates_collapse_case_insensitively(self, candidates):
        text = "How much does GEP procurement software cost for an enterprise?"
        matches = [c for c in candidates if c.prompt_text.lower() == text.lower()]
        assert len(matches) == 1
        # The Semrush row is added before the template, so the row's data wins.
        assert matches[0].keyword_source is KeywordSource.PHRASE_QUESTIONS
        assert matches[0].search_volume == 30

    def test_prompts_are_capitalised_and_end_with_question_mark(self, candidates):
        for candidate in candidates:
            assert candidate.prompt_text[0].isupper()
            assert candidate.prompt_text[-1] in "?.!"
        assert _by_text(candidates, "How does procurement software work?")
        assert _by_text(candidates, "Which procurement software is best?")
        assert _by_text(candidates, "Why buy procurement software.")

    def test_as_question_collapses_whitespace_and_handles_empty(self):
        assert _as_question("  what   is  it ") == "What is it?"
        assert _as_question("already asked?") == "Already asked?"
        assert _as_question("   ") == ""


class TestSubtopic:
    def test_defaults_to_title_cased_seed(self, candidates):
        assert {c.subtopic for c in candidates} == {"Procurement Software"}

    def test_picks_analyst_subtopic_sharing_most_words(self, rows):
        client = _client(["Source to Pay", "Procurement Platforms", "Contract Management"])
        result = PromptGenerator().from_keywords(client, {SEED: 1}, {SEED: rows[:1]})
        assert {c.subtopic for c in result} == {"Procurement Platforms"}

    def test_falls_back_to_first_subtopic_when_nothing_overlaps(self, rows):
        client = _client(["Analytics", "Reporting"])
        result = PromptGenerator().from_keywords(client, {SEED: 1}, {SEED: rows[:1]})
        assert {c.subtopic for c in result} == {"Analytics"}


class TestKept:
    def test_filters_on_keep_action(self, candidates):
        surviving = kept(candidates)
        assert surviving
        assert all(c.intent.action is IntentAction.KEEP for c in surviving)
        assert len(surviving) < len(candidates)

    def test_missing_seed_volume_defaults_to_zero(self):
        result = PromptGenerator().from_keywords(_client(), {}, {})
        assert result
        assert all(c.search_volume == 0 for c in result)
