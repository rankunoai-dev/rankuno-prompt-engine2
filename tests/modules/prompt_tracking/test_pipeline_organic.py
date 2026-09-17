"""Pipeline tests for Google organic rank tracking (prompt text + seed keyword).

The Google engine is a real `SerpApiClient` on a mock transport so the pipeline
exercises `search_and_ask()`; the other engines stay stubbed.
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import httpx
import pytest

from src.core.rate_limiter import CostLedger
from src.integrations.schemas import Engine
from src.integrations.serp_api import SerpApiClient
from src.modules.prompt_tracking.pipeline import PromptTrackerPipeline
from src.modules.prompt_tracking.schemas import PipelineInput, RankQueryKind
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB
from tests.modules.prompt_tracking.test_pipeline import SEED, StubEngine, _semrush, client

__all__ = ["client"]  # re-exported fixture

ORGANIC = [
    {"position": 1, "link": "https://www.coupa.com/", "title": "Coupa"},
    {"position": 2, "link": "https://www.gep.com/software/procurement-software"},
    {"position": 3, "link": "https://www.sap.com/ariba"},
]
AIO = {
    "text_blocks": [{"type": "paragraph", "snippet": "Procurement software helps."}],
    "references": [{"link": "https://www.gep.com/knowledge/x", "title": "GEP", "index": 0}],
}


class SerpRouter:
    """Serves SerpApi payloads and records every query."""

    def __init__(self, *, status: int = 200, keyword_status: int = 200) -> None:
        self.queries: list[str] = []
        self.status = status
        self.keyword_status = keyword_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        query = request.url.params["q"]
        self.queries.append(query)
        status = self.keyword_status if query == SEED else self.status
        if status != 200:
            return httpx.Response(status, text="serp down")
        return httpx.Response(200, json={"organic_results": ORGANIC, "ai_overview": AIO})


def _serp_client(settings, router: SerpRouter) -> SerpApiClient:
    return SerpApiClient(settings, transport=httpx.MockTransport(router))


def _tool(guardrails, ledger, settings, db, router: SerpRouter, extra: list[Engine]):
    engines = {e: StubEngine(e) for e in extra}
    engines[Engine.GOOGLE_AI_OVERVIEW] = _serp_client(settings, router)
    return PromptTrackerPipeline(
        guardrails, ledger, settings=settings, semrush=_semrush(settings), engines=engines, db=db
    )


def _organic_rows(db_path: Path) -> list[tuple[str, int | None]]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT query_kind, client_position FROM organic_snapshots ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


@pytest.fixture
def db(tmp_path) -> TimeSeriesDB:
    return TimeSeriesDB(tmp_path / "db" / "tracker.sqlite")


class TestBothRankKinds:
    @pytest.fixture
    def outcome(self, permissive_guardrails, settings, client, db):
        ledger = CostLedger(ceiling_usd=100.0)
        router = SerpRouter()
        tool = _tool(permissive_guardrails, ledger, settings, db, router, [Engine.PERPLEXITY])
        result = tool.run(
            PipelineInput(
                client=client,
                engines=[Engine.GOOGLE_AI_OVERVIEW, Engine.PERPLEXITY],
                samples_per_engine=2,
            )
        )
        assert result.ok, result.error
        return result.data, router, ledger

    def test_prompt_rank_rides_on_the_overview_call(self, outcome):
        summary, router, _ = outcome
        assert summary.prompts_selected == 20
        for record in summary.records:
            prompt_rank = record.latest_rank(RankQueryKind.PROMPT)
            assert prompt_rank is not None
            assert prompt_rank.query == record.prompt_text
            assert prompt_rank.samples == 2
            assert prompt_rank.client_position == 2
            assert prompt_rank.client_url == "https://www.gep.com/software/procurement-software"
            assert prompt_rank.top_domains == ["coupa.com", "gep.com", "sap.com"]
            assert prompt_rank.competitor_positions == {"coupa.com": 1}
        # 20 prompts x 2 samples for the overview, plus exactly one keyword lookup.
        assert router.queries.count(SEED) == 1
        assert len(router.queries) == 20 * 2 + 1

    def test_keyword_rank_is_looked_up_once_per_distinct_keyword(self, outcome, settings):
        summary, _, ledger = outcome
        assert summary.keyword_rank_calls == 1
        keyword_ranks = {id(r.latest_rank(RankQueryKind.KEYWORD)) for r in summary.records}
        assert len(keyword_ranks) == 1  # the same cached snapshot on every record
        for record in summary.records:
            keyword_rank = record.latest_rank(RankQueryKind.KEYWORD)
            assert keyword_rank is not None
            assert keyword_rank.query == SEED
            assert keyword_rank.query_kind is RankQueryKind.KEYWORD
            assert keyword_rank.client_position == 2
        engine_spend = 20 * 2 * (settings.cost_serpapi_call_usd + settings.cost_perplexity_call_usd)
        keyword_spend = settings.cost_serpapi_call_usd
        assert summary.estimated_cost_usd == pytest.approx(engine_spend + keyword_spend, abs=1e-4)

    def test_persisted_and_reported(self, outcome, db, tmp_path):
        summary, _, _ = outcome
        rows = _organic_rows(tmp_path / "db" / "tracker.sqlite")
        assert len(rows) == 20 * 2
        assert {kind for kind, _ in rows} == {"PROMPT", "KEYWORD"}
        assert all(position == 2 for _, position in rows)
        first = summary.records[0]
        assert len(db.organic_history(first.prompt_id, RankQueryKind.KEYWORD)) == 1

        with Path(summary.report_path).open(newline="", encoding="utf-8") as handle:
            sheet = list(csv.reader(handle))
        header, row = sheet[0], sheet[1]
        assert row[header.index("Google Organic Rank (Prompt)")] == "2"
        assert row[header.index("Google Organic Rank (Keyword)")] == "2"
        assert row[header.index("Ranking URL (Keyword)")].endswith("/procurement-software")


class TestSwitches:
    def test_keyword_rank_can_be_disabled(self, permissive_guardrails, settings, client, db):
        router = SerpRouter()
        tool = _tool(permissive_guardrails, CostLedger(ceiling_usd=100.0), settings, db, router, [])
        result = tool.run(
            PipelineInput(
                client=client,
                engines=[Engine.GOOGLE_AI_OVERVIEW],
                samples_per_engine=1,
                track_keyword_rank=False,
            )
        )
        assert result.ok, result.error
        assert result.data.keyword_rank_calls == 0
        assert SEED not in router.queries
        assert all(r.latest_rank(RankQueryKind.KEYWORD) is None for r in result.data.records)
        assert all(r.latest_rank(RankQueryKind.PROMPT) is not None for r in result.data.records)

    def test_keyword_rank_works_without_the_overview_engine(
        self, permissive_guardrails, settings, client, db
    ):
        """Restricting engines to ChatGPT still yields keyword rank via the Google client."""
        router = SerpRouter()
        tool = _tool(
            permissive_guardrails,
            CostLedger(ceiling_usd=100.0),
            settings,
            db,
            router,
            [Engine.CHATGPT_SEARCH],
        )
        result = tool.run(
            PipelineInput(client=client, engines=[Engine.CHATGPT_SEARCH], samples_per_engine=1)
        )
        assert result.ok, result.error
        assert result.data.keyword_rank_calls == 1
        assert router.queries == [SEED]
        for record in result.data.records:
            assert record.latest_rank(RankQueryKind.PROMPT) is None
            assert record.latest_rank(RankQueryKind.KEYWORD) is not None

    def test_skipping_the_audit_skips_organic_too(
        self, permissive_guardrails, settings, client, db
    ):
        router = SerpRouter()
        tool = _tool(permissive_guardrails, CostLedger(ceiling_usd=100.0), settings, db, router, [])
        result = tool.run(PipelineInput(client=client, skip_engine_audit=True))
        assert result.ok, result.error
        assert router.queries == []
        assert result.data.keyword_rank_calls == 0
        assert all(r.organic_history == [] for r in result.data.records)


class TestFailureModes:
    def test_keyword_lookup_failure_is_a_warning_and_still_costs(
        self, permissive_guardrails, settings, client, db
    ):
        router = SerpRouter(keyword_status=500)
        tool = _tool(
            permissive_guardrails,
            CostLedger(ceiling_usd=100.0),
            settings,
            db,
            router,
            [Engine.CHATGPT_SEARCH],
        )
        result = tool.run(
            PipelineInput(client=client, engines=[Engine.CHATGPT_SEARCH], samples_per_engine=1)
        )
        assert result.ok, result.error
        summary = result.data
        assert summary.keyword_rank_calls == 1
        assert router.queries == [SEED]  # failure is cached; no retry per prompt
        assert any("Keyword rank lookup failed" in w for w in summary.warnings)
        assert all(r.latest_rank(RankQueryKind.KEYWORD) is None for r in summary.records)
        expected = 20 * settings.cost_openai_search_call_usd + settings.cost_serpapi_call_usd
        assert summary.estimated_cost_usd == pytest.approx(expected, abs=1e-4)

    def test_budget_exhausted_at_keyword_lookup_is_reported(
        self, permissive_guardrails, settings, client, db
    ):
        reservation = PromptTrackerPipeline.metadata.estimated_cost_usd
        semrush_spend = 530 * settings.cost_semrush_unit_usd
        # Enough for the reservation, Semrush and all 20 ChatGPT calls, not the keyword call.
        ceiling = reservation + semrush_spend + 20 * settings.cost_openai_search_call_usd + 0.001
        router = SerpRouter()
        tool = _tool(
            permissive_guardrails,
            CostLedger(ceiling_usd=ceiling),
            settings,
            db,
            router,
            [Engine.CHATGPT_SEARCH],
        )
        result = tool.run(
            PipelineInput(client=client, engines=[Engine.CHATGPT_SEARCH], samples_per_engine=1)
        )
        assert result.ok, result.error
        summary = result.data
        assert summary.keyword_rank_calls == 0
        assert router.queries == []
        assert summary.engine_calls == 20
        assert any("Keyword rank lookups stopped" in w for w in summary.warnings)
        assert summary.prompts_selected == 20

    def test_stub_overview_engine_skips_keyword_rank_silently(
        self, permissive_guardrails, settings, client, db
    ):
        engines = {e: StubEngine(e) for e in (Engine.GOOGLE_AI_OVERVIEW, Engine.GEMINI)}
        tool = PromptTrackerPipeline(
            permissive_guardrails,
            CostLedger(ceiling_usd=100.0),
            settings=settings,
            semrush=_semrush(settings),
            engines=engines,
            db=db,
        )
        result = tool.run(
            PipelineInput(client=client, samples_per_engine=1, engines=[Engine.GEMINI])
        )
        assert result.ok, result.error
        assert result.data.keyword_rank_calls == 0
        assert result.data.warnings == []


class TestDescribeInvocation:
    def test_mentions_keyword_rank_calls(self, permissive_guardrails, settings, client, db):
        tool = _tool(
            permissive_guardrails, CostLedger(ceiling_usd=100.0), settings, db, SerpRouter(), []
        )
        text = tool.describe_invocation(PipelineInput(client=client))
        assert "up to 1 keyword rank calls" in text
        assert f"(${settings.cost_serpapi_call_usd:.2f})" in text

        text = tool.describe_invocation(PipelineInput(client=client, track_keyword_rank=False))
        assert "($0.00)" in text
        text = tool.describe_invocation(PipelineInput(client=client, skip_engine_audit=True))
        assert "projected engine spend $0.00" in text
