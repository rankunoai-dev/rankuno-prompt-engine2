"""Tests for the connector data contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.integrations.schemas import Citation, Engine, EngineAnswer, KeywordRecord, KeywordSource


def _citation(url: str, domain: str, position: int) -> Citation:
    return Citation(url=url, domain=domain, position=position)


def test_cited_domains_dedupes_in_citation_order():
    answer = EngineAnswer(
        engine=Engine.PERPLEXITY,
        model="sonar-pro",
        prompt="what is procurement",
        web_triggered=True,
        citations=[
            _citation("https://gep.com/a", "gep.com", 1),
            _citation("https://en.wikipedia.org/x", "wikipedia.org", 2),
            _citation("https://blog.gep.com/b", "gep.com", 3),
            _citation("https://example.org/", "example.org", 4),
        ],
    )
    assert answer.cited_domains == ["gep.com", "wikipedia.org", "example.org"]


def test_cited_domains_is_empty_without_citations():
    answer = EngineAnswer(engine=Engine.GEMINI, model="m", prompt="p", web_triggered=False)
    assert answer.cited_domains == []
    assert answer.consulted_urls == []
    assert answer.captured_at.tzinfo is not None


@pytest.mark.parametrize("position", [0, -1])
def test_citation_position_must_be_at_least_one(position):
    with pytest.raises(ValidationError):
        Citation(url="https://gep.com/", domain="gep.com", position=position)


def test_citation_defaults_to_resolved():
    citation = _citation("https://gep.com/", "gep.com", 1)
    assert citation.resolved is True
    assert citation.title is None


def test_strict_model_rejects_unknown_fields():
    with pytest.raises(ValidationError, match="extra"):
        Citation(url="https://gep.com/", domain="gep.com", position=1, score=0.9)
    with pytest.raises(ValidationError, match="extra"):
        EngineAnswer(
            engine=Engine.CHATGPT_SEARCH,
            model="m",
            prompt="p",
            web_triggered=True,
            raw_payload={},
        )


def test_engine_answer_requires_web_triggered():
    with pytest.raises(ValidationError, match="web_triggered"):
        EngineAnswer(engine=Engine.CHATGPT_SEARCH, model="m", prompt="p")


def test_keyword_record_bounds_competition():
    with pytest.raises(ValidationError):
        KeywordRecord(
            keyword="k",
            search_volume=10,
            competition=1.5,
            database="us",
            source=KeywordSource.PHRASE_ALL,
        )
