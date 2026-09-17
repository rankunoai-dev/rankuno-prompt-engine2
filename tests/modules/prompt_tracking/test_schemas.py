"""Tests for the prompt-tracking data contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from src.integrations.schemas import Engine
from src.modules.prompt_tracking.schemas import (
    CitationSnapshot,
    ClientProfile,
    DecisionStage,
    MasterPromptRecord,
    PromptType,
    SearchIntent,
    Verdict,
    prompt_id_for,
)

_NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


def _snapshot(engine: Engine, captured_at: datetime, model: str = "m") -> CitationSnapshot:
    return CitationSnapshot(
        engine=engine,
        model=model,
        captured_at=captured_at,
        samples=3,
        web_trigger_rate=1.0,
        client_cited_samples=1,
        client_citation_rate=0.33,
        client_cited=False,
    )


def _record(history: list[CitationSnapshot]) -> MasterPromptRecord:
    return MasterPromptRecord(
        prompt_id=prompt_id_for("LOB", "What is procurement software?"),
        lob="LOB",
        subtopic="Procurement Software",
        core_keyword="procurement software",
        search_volume=100,
        prompt_text="What is procurement software?",
        search_intent=SearchIntent.INFORMATIONAL,
        decision_stage=DecisionStage.AWARENESS,
        prompt_type=PromptType.NON_BRANDED,
        web_triggers=True,
        citation_history=history,
        verdict=Verdict.KEEP,
        verdict_reason="test",
    )


class TestPromptId:
    def test_is_stable_and_sixteen_hex_chars(self):
        first = prompt_id_for("Procurement Software", "What is procurement software?")
        assert first == prompt_id_for("Procurement Software", "What is procurement software?")
        assert len(first) == 16
        assert int(first, 16) >= 0

    def test_ignores_case_and_surrounding_whitespace(self):
        base = prompt_id_for("Procurement Software", "What is procurement software?")
        assert prompt_id_for("  procurement software ", " WHAT IS PROCUREMENT SOFTWARE? ") == base

    def test_differs_by_lob(self):
        text = "What is procurement software?"
        assert prompt_id_for("Procurement", text) != prompt_id_for("Supply Chain", text)


class TestClientProfile:
    def test_strips_blanks_from_list_fields(self):
        profile = ClientProfile(
            brand_name="GEP",
            aliases=[" GEP SMART ", "", "   "],
            domains=["gep.com ", " "],
            competitor_domains=["", " coupa.com"],
            lob="Procurement",
            seed_keywords=[" procurement software ", ""],
            landing_pages=[" https://www.gep.com/ ", ""],
        )
        assert profile.aliases == ["GEP SMART"]
        assert profile.domains == ["gep.com"]
        assert profile.competitor_domains == ["coupa.com"]
        assert profile.seed_keywords == ["procurement software"]
        assert profile.landing_pages == ["https://www.gep.com/"]

    def test_requires_at_least_one_domain(self):
        with pytest.raises(ValidationError):
            ClientProfile(brand_name="GEP", domains=[], lob="X", seed_keywords=["k"])

    def test_requires_at_least_one_seed_keyword(self):
        with pytest.raises(ValidationError):
            ClientProfile(brand_name="GEP", domains=["gep.com"], lob="X", seed_keywords=[])

    def test_brand_terms_longest_first(self):
        profile = ClientProfile(
            brand_name="GEP",
            aliases=["GEP SMART", "NEXXE", "GEP"],
            domains=["gep.com"],
            lob="X",
            seed_keywords=["k"],
        )
        terms = profile.brand_terms()
        assert terms[0] == "GEP SMART"
        assert terms[-1] == "GEP"
        assert len(terms) == 3
        assert [len(t) for t in terms] == sorted((len(t) for t in terms), reverse=True)


class TestMasterPromptRecord:
    def test_latest_returns_newest_snapshot_for_engine(self):
        older = _snapshot(Engine.GEMINI, _NOW - timedelta(days=1), model="old")
        newer = _snapshot(Engine.GEMINI, _NOW, model="new")
        other = _snapshot(Engine.PERPLEXITY, _NOW + timedelta(days=1), model="other")
        record = _record([older, other, newer])
        assert record.latest(Engine.GEMINI) is newer
        assert record.latest(Engine.PERPLEXITY) is other

    def test_latest_returns_none_when_engine_has_no_snapshot(self):
        record = _record([_snapshot(Engine.GEMINI, _NOW)])
        assert record.latest(Engine.CHATGPT_SEARCH) is None
        assert _record([]).latest(Engine.GEMINI) is None


class TestCitationSnapshotBounds:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"web_trigger_rate": 1.5},
            {"web_trigger_rate": -0.1},
            {"client_citation_rate": 1.01},
            {"client_citation_rate": -0.5},
            {"samples": 0},
            {"failed_samples": -1},
            {"client_best_rank": 0},
            {"client_mean_rank": 0.5},
        ],
    )
    def test_out_of_range_values_are_rejected(self, overrides):
        base = {
            "engine": Engine.GEMINI,
            "model": "m",
            "samples": 1,
            "web_trigger_rate": 0.0,
            "client_cited_samples": 0,
            "client_citation_rate": 0.0,
            "client_cited": False,
        }
        with pytest.raises(ValidationError):
            CitationSnapshot(**{**base, **overrides})

    def test_boundaries_are_inclusive(self):
        snap = CitationSnapshot(
            engine=Engine.GEMINI,
            model="m",
            samples=1,
            web_trigger_rate=1.0,
            client_cited_samples=1,
            client_citation_rate=1.0,
            client_cited=True,
            client_best_rank=1,
            client_mean_rank=1.0,
        )
        assert snap.client_best_rank == 1
        assert snap.captured_at.tzinfo is not None
