"""Tests for folding sampled engine answers into a citation snapshot."""

from __future__ import annotations

import pytest

from src.integrations.schemas import Citation, Engine, EngineAnswer
from src.modules.prompt_tracking.citations import build_snapshot, client_rank
from src.modules.prompt_tracking.schemas import ClientProfile


def _citation(domain: str, position: int) -> Citation:
    return Citation(url=f"https://{domain}/page-{position}", domain=domain, position=position)


def _answer(
    *domains: str, web_triggered: bool = True, text: str = "answer", model: str = "stub-model"
) -> EngineAnswer:
    return EngineAnswer(
        engine=Engine.PERPLEXITY,
        model=model,
        prompt="What is procurement software?",
        answer_text=text,
        web_triggered=web_triggered,
        citations=[_citation(d, i + 1) for i, d in enumerate(domains)],
    )


@pytest.fixture
def client() -> ClientProfile:
    return ClientProfile(
        brand_name="GEP",
        domains=["gep.com"],
        competitor_domains=["coupa.com", "https://www.jaggaer.com/"],
        lob="Procurement Software",
        seed_keywords=["procurement software"],
    )


class TestClientRank:
    def test_subdomain_of_client_counts(self):
        assert client_rank(_answer("coupa.com", "blog.gep.com"), ["gep.com"]) == 2

    def test_lookalike_does_not_count(self):
        assert client_rank(_answer("notgep.com", "gep.com.evil.example"), ["gep.com"]) is None

    def test_first_client_citation_wins(self):
        assert client_rank(_answer("gep.com", "blog.gep.com"), ["gep.com"]) == 1

    def test_matches_any_client_domain(self):
        assert client_rank(_answer("sap.com", "nexxe.com"), ["gep.com", "nexxe.com"]) == 2

    def test_no_citations_returns_none(self):
        assert client_rank(_answer(), ["gep.com"]) is None


class TestBuildSnapshot:
    @pytest.fixture
    def answers(self) -> list[EngineAnswer]:
        return [
            _answer("gep.com", "coupa.com", text="x" * 500),
            _answer("coupa.com", "www.jaggaer.com", "gep.com"),
            _answer("coupa.com", "sap.com", web_triggered=False),
        ]

    def test_client_metrics(self, answers, client):
        snap = build_snapshot(Engine.PERPLEXITY, answers, client)
        assert snap.engine is Engine.PERPLEXITY
        assert snap.model == "stub-model"
        assert snap.samples == 3
        assert snap.failed_samples == 0
        assert snap.client_cited_samples == 2
        assert snap.client_citation_rate == pytest.approx(2 / 3)
        assert snap.client_cited is True
        assert snap.client_best_rank == 1
        assert snap.client_mean_rank == pytest.approx(2.0)

    def test_competitor_best_ranks_sorted_by_rank(self, answers, client):
        snap = build_snapshot(Engine.PERPLEXITY, answers, client)
        assert snap.competitor_citations == {"coupa.com": 1, "jaggaer.com": 2}
        assert list(snap.competitor_citations) == ["coupa.com", "jaggaer.com"]

    def test_cited_domains_ordered_by_frequency_then_first_appearance(self, answers, client):
        snap = build_snapshot(Engine.PERPLEXITY, answers, client)
        assert snap.cited_domains == ["coupa.com", "gep.com", "www.jaggaer.com", "sap.com"]

    def test_web_trigger_rate(self, answers, client):
        snap = build_snapshot(Engine.PERPLEXITY, answers, client)
        assert snap.web_trigger_rate == pytest.approx(2 / 3)

    def test_excerpt_is_truncated_to_300_chars(self, answers, client):
        snap = build_snapshot(Engine.PERPLEXITY, answers, client)
        assert len(snap.answer_excerpt) == 300
        assert snap.answer_excerpt == "x" * 300

    def test_cited_in_fewer_than_half_is_not_cited(self, client):
        answers = [_answer("gep.com"), _answer("coupa.com"), _answer("sap.com")]
        snap = build_snapshot(Engine.GEMINI, answers, client)
        assert snap.client_cited_samples == 1
        assert snap.client_cited is False
        assert snap.client_best_rank == 1

    def test_failed_samples_are_counted_in_total_but_not_in_rates(self, client):
        snap = build_snapshot(Engine.GEMINI, [_answer("gep.com")], client, failed_samples=2)
        assert snap.samples == 3
        assert snap.failed_samples == 2
        assert snap.client_citation_rate == pytest.approx(1.0)
        assert snap.client_cited is True

    def test_all_samples_failed(self, client):
        snap = build_snapshot(Engine.CHATGPT_SEARCH, [], client, failed_samples=3)
        assert snap.samples == 3
        assert snap.failed_samples == 3
        assert snap.model == "unavailable"
        assert snap.web_trigger_rate == pytest.approx(0.0)
        assert snap.client_citation_rate == pytest.approx(0.0)
        assert snap.client_cited is False
        assert snap.client_best_rank is None
        assert snap.client_mean_rank is None
        assert snap.cited_domains == []
        assert snap.answer_excerpt == ""

    def test_zero_attempted_samples_is_an_error(self, client):
        with pytest.raises(ValueError, match="at least one attempted sample"):
            build_snapshot(Engine.GEMINI, [], client)

    def test_no_competitors_configured(self):
        client = ClientProfile(brand_name="GEP", domains=["gep.com"], lob="X", seed_keywords=["k"])
        snap = build_snapshot(Engine.GEMINI, [_answer("coupa.com", "gep.com")], client)
        assert snap.competitor_citations == {}
        assert snap.client_best_rank == 2
