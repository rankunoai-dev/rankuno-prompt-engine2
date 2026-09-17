"""End-to-end tests for the governed tracker pipeline with every dependency stubbed.

Semrush is mocked at the httpx transport, engines are duck-typed stubs, the
redirect resolver is a fake, and the time-series store lives under `tmp_path`.
Nothing here touches the network.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from src.core.errors import IntegrationError
from src.core.rate_limiter import CostLedger
from src.core.schemas import ExecutionStatus
from src.integrations.gemini_search import GEMINI_REDIRECT_HOST, GeminiSearchClient
from src.integrations.openai_search import OpenAISearchClient
from src.integrations.schemas import Citation, Engine, EngineAnswer
from src.integrations.semrush import SemrushClient
from src.integrations.url_resolver import ResolvedUrl
from src.modules.prompt_tracking.pipeline import PromptTrackerPipeline
from src.modules.prompt_tracking.schemas import (
    ClientProfile,
    PipelineInput,
    PromptType,
    Verdict,
)
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB

SEED = "procurement software"
LOB = "Procurement Software"
PROCUREMENT_URL = "https://www.gep.com/software/procurement-software"

_CSV_HEADER = "Keyword;Search Volume;CPC;Competition;Number of Results"
_PHRASE_ALL_ROWS = [f"{SEED};5400;12.50;0.85;150000000"]
# Deliberate mix: three Layer-1 noise rows, eight software-intent questions and
# two branded questions, so both prompt types can reach their quota of ten.
_QUESTION_ROWS = [
    "procurement software jobs in new york;590;0.10;0.05;1000",
    "procurement software salary;320;0.05;0.02;900",
    "top 10 procurement software companies;210;1.20;0.30;800",
    "what is procurement software;880;9.10;0.60;5000",
    "how much does procurement software cost;480;14.00;0.90;4000",
    "what is the best procurement software for small business;390;11.00;0.80;3500",
    "how does procurement software integrate with erp;260;8.00;0.50;2100",
    "which procurement software is best for manufacturing;170;7.50;0.45;1800",
    "what features should procurement software have;140;6.00;0.40;1500",
    "is cloud procurement software secure;90;5.00;0.35;1200",
    "how to choose procurement software;70;4.00;0.30;1000",
    "is gep smart good for procurement software;40;3.00;0.20;600",
    "what does gep smart procurement software cost;30;2.50;0.15;500",
]
SEMRUSH_UNITS = 10 * len(_PHRASE_ALL_ROWS) + 40 * len(_QUESTION_ROWS)


def _csv(rows: list[str]) -> str:
    return "\n".join([_CSV_HEADER, *rows]) + "\n"


def _semrush(settings, *, status: int = 200) -> SemrushClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, text="upstream broke")
        report = request.url.params.get("type")
        body = _csv(_PHRASE_ALL_ROWS if report == "phrase_all" else _QUESTION_ROWS)
        return httpx.Response(200, text=body)

    return SemrushClient(settings, transport=httpx.MockTransport(handler))


class StubEngine:
    """Duck-typed engine connector: records calls and returns canned answers."""

    def __init__(
        self,
        engine: Engine,
        *,
        citations: list[Citation] | None = None,
        web_triggered: bool = True,
        fail: bool = False,
    ) -> None:
        self.engine = engine
        self.calls = 0
        self._fail = fail
        self._web = web_triggered
        self._citations = (
            citations
            if citations is not None
            else [
                Citation(url="https://www.gep.com/software", domain="gep.com", position=1),
                Citation(url="https://www.coupa.com/", domain="coupa.com", position=2),
            ]
        )

    def ask(self, prompt: str) -> EngineAnswer:
        self.calls += 1
        if self._fail:
            raise IntegrationError("stub", "engine down")
        return EngineAnswer(
            engine=self.engine,
            model="stub-model",
            prompt=prompt,
            answer_text="GEP SMART is a procurement platform.",
            web_triggered=self._web,
            citations=[c.model_copy() for c in self._citations],
        )


class FakeResolver:
    """Resolves anything under the Gemini redirect host to the client's site."""

    def __init__(self) -> None:
        self.requested: list[str] = []

    def resolve(self, url: str) -> ResolvedUrl:
        self.requested.append(url)
        if "unresolvable" in url:
            return ResolvedUrl(requested=url, final_url=url, hops=0, resolved=False, reason="x")
        if "hostless" in url:
            # Resolved, but to a URL with no host: no domain can be attributed.
            return ResolvedUrl(requested=url, final_url="https://", hops=1, resolved=True)
        return ResolvedUrl(requested=url, final_url="https://www.gep.com/x", hops=1, resolved=True)


@pytest.fixture
def client() -> ClientProfile:
    return ClientProfile(
        brand_name="GEP",
        aliases=["GEP SMART"],
        domains=["gep.com"],
        competitor_domains=["coupa.com"],
        lob=LOB,
        seed_keywords=[SEED],
        landing_pages=[PROCUREMENT_URL, "https://www.gep.com/about-us"],
    )


@pytest.fixture
def db(tmp_path) -> TimeSeriesDB:
    return TimeSeriesDB(tmp_path / "db" / "tracker.sqlite")


@pytest.fixture
def engines() -> dict[Engine, StubEngine]:
    return {engine: StubEngine(engine) for engine in Engine}


@pytest.fixture
def ledger() -> CostLedger:
    return CostLedger(ceiling_usd=100.0)


def _pipeline(guardrails, ledger, settings, **overrides) -> PromptTrackerPipeline:
    kwargs = {"semrush": _semrush(settings)}
    kwargs.update(overrides)
    # Keyword-rank lookups lazily build a real SerpApi client unless the Google
    # engine is injected; a stub keeps these tests off the network. Stubs are
    # not SerpApi clients, so the lookup is skipped and costs nothing.
    engines = dict(kwargs.get("engines") or {})
    engines.setdefault(Engine.GOOGLE_AI_OVERVIEW, StubEngine(Engine.GOOGLE_AI_OVERVIEW))
    kwargs["engines"] = engines
    return PromptTrackerPipeline(guardrails, ledger, settings=settings, **kwargs)


def _engine_cost(settings, engine_list: list[Engine]) -> float:
    per_call = {
        Engine.CHATGPT_SEARCH: settings.cost_openai_search_call_usd,
        Engine.PERPLEXITY: settings.cost_perplexity_call_usd,
        Engine.GEMINI: settings.cost_gemini_grounded_call_usd,
        Engine.GOOGLE_AI_OVERVIEW: settings.cost_serpapi_call_usd,
    }
    return sum(per_call[e] for e in engine_list)


def _count(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608
    finally:
        conn.close()


class TestHappyPath:
    @pytest.fixture
    def outcome(self, permissive_guardrails, ledger, settings, client, engines, db):
        tool = _pipeline(permissive_guardrails, ledger, settings, engines=engines, db=db)
        result = tool.run(PipelineInput(client=client, samples_per_engine=2))
        return result, ledger, engines

    def test_selects_twenty_prompts(self, outcome):
        result, _, _ = outcome
        assert result.ok, result.error
        summary = result.data
        assert summary.prompts_selected == 20
        assert len(summary.records) == 20
        by_type = [r.prompt_type for r in summary.records]
        assert by_type.count(PromptType.BRANDED) == 10
        assert by_type.count(PromptType.NON_BRANDED) == 10
        assert summary.candidates_generated > summary.candidates_kept >= 20
        assert summary.warnings == []
        assert summary.lob == LOB
        assert summary.brand_name == "GEP"
        assert summary.finished_at >= summary.started_at

    def test_engine_calls_and_spend(self, outcome, settings):
        result, ledger, engines = outcome
        summary = result.data
        expected_calls = 20 * len(Engine) * 2
        assert summary.engine_calls == expected_calls
        assert summary.failed_engine_calls == 0
        assert all(stub.calls == 20 * 2 for stub in engines.values())

        engine_spend = 20 * 2 * _engine_cost(settings, list(Engine))
        assert summary.estimated_cost_usd == pytest.approx(engine_spend, abs=1e-4)
        assert summary.semrush_units == SEMRUSH_UNITS
        assert summary.semrush_units > 0
        semrush_spend = SEMRUSH_UNITS * settings.cost_semrush_unit_usd
        reservation = PromptTrackerPipeline.metadata.estimated_cost_usd
        assert ledger.spent_usd == pytest.approx(reservation + engine_spend + semrush_spend)

    def test_every_record_has_one_snapshot_per_engine(self, outcome):
        result, _, _ = outcome
        for record in result.data.records:
            assert len(record.citation_history) == len(Engine)
            assert {s.engine for s in record.citation_history} == set(Engine)
            assert record.web_triggers is True
            for snap in record.citation_history:
                assert snap.samples == 2
                assert snap.failed_samples == 0
                assert snap.client_cited is True
                assert snap.client_best_rank == 1
                assert snap.client_citation_rate == pytest.approx(1.0)
                assert snap.competitor_citations == {"coupa.com": 2}
                assert snap.model == "stub-model"
            assert record.verdict is Verdict.KEEP
            assert "client cited" in record.verdict_reason

    def test_landing_pages_are_mapped(self, outcome):
        result, _, _ = outcome
        assert all(r.mapped_url == PROCUREMENT_URL for r in result.data.records)
        assert not any(r.content_gap for r in result.data.records)

    def test_report_is_written_under_reports_dir(self, outcome, settings):
        result, _, _ = outcome
        path = Path(result.data.report_path)
        assert path.exists()
        assert path.parent == Path(settings.reports_dir)
        assert path.name.startswith("master-prompts-procurement-software-")
        assert path.suffix == ".csv"
        assert len(path.read_text(encoding="utf-8").splitlines()) == 21

    def test_database_holds_prompts_snapshots_and_run(self, outcome, db, tmp_path):
        result, _, _ = outcome
        db_path = tmp_path / "db" / "tracker.sqlite"
        assert len(db.prompt_ids(LOB)) == 20
        assert _count(db_path, "prompts") == 20
        assert _count(db_path, "snapshots") == 20 * len(Engine)
        assert _count(db_path, "runs") == 1
        first = result.data.records[0]
        assert len(db.history(first.prompt_id, Engine.GEMINI)) == 1

    def test_governance_envelope(self, outcome):
        result, _, _ = outcome
        assert result.status is ExecutionStatus.SUCCESS
        assert result.requires_human_review is True
        assert result.tool == "prompt_tracking.master_tracker"


class TestSkipEngineAudit:
    def test_no_engine_calls_and_no_snapshots(
        self, permissive_guardrails, ledger, settings, client, engines
    ):
        tool = _pipeline(permissive_guardrails, ledger, settings, engines=engines)
        result = tool.run(PipelineInput(client=client, skip_engine_audit=True, write_report=False))
        assert result.ok, result.error
        summary = result.data
        assert summary.engine_calls == 0
        assert summary.failed_engine_calls == 0
        assert all(stub.calls == 0 for stub in engines.values())
        assert summary.prompts_selected == 20
        assert summary.report_path is None
        for record in summary.records:
            assert record.citation_history == []
            assert record.web_triggers is False
            assert record.verdict is Verdict.KEEP
            assert "audit not run" in record.verdict_reason
        assert summary.estimated_cost_usd == pytest.approx(0.0)
        reservation = PromptTrackerPipeline.metadata.estimated_cost_usd
        semrush_spend = SEMRUSH_UNITS * settings.cost_semrush_unit_usd
        assert ledger.spent_usd == pytest.approx(reservation + semrush_spend)
        # Without an injected store the configured path is used.
        assert Path(settings.tracker_db_path).exists()
        assert _count(Path(settings.tracker_db_path), "prompts") == 20


class TestEngineFailures:
    def test_failed_calls_are_counted_and_run_still_succeeds(
        self, permissive_guardrails, ledger, settings, client, db
    ):
        chosen = [Engine.CHATGPT_SEARCH, Engine.PERPLEXITY]
        engines = {e: StubEngine(e, fail=True) for e in chosen}
        tool = _pipeline(permissive_guardrails, ledger, settings, engines=engines, db=db)
        result = tool.run(PipelineInput(client=client, engines=chosen, samples_per_engine=2))
        assert result.ok, result.error
        summary = result.data
        assert summary.engine_calls == 20 * 2 * 2
        assert summary.failed_engine_calls == summary.engine_calls
        # Spend is charged before the call, so failures still cost.
        assert summary.estimated_cost_usd == pytest.approx(20 * 2 * _engine_cost(settings, chosen))
        for record in summary.records:
            assert len(record.citation_history) == 2
            for snap in record.citation_history:
                assert snap.failed_samples == 2
                assert snap.samples == 2
                assert snap.model == "unavailable"
                assert snap.client_cited is False
                assert snap.web_trigger_rate == pytest.approx(0.0)
            assert record.web_triggers is False
            assert record.verdict is Verdict.KEEP
            assert "keyword has demand" in record.verdict_reason

    def test_web_grounded_but_not_cited_is_a_visibility_gap(
        self, permissive_guardrails, ledger, settings, client, db
    ):
        only_competitor = [Citation(url="https://coupa.com/", domain="coupa.com", position=1)]
        engines = {Engine.PERPLEXITY: StubEngine(Engine.PERPLEXITY, citations=only_competitor)}
        tool = _pipeline(permissive_guardrails, ledger, settings, engines=engines, db=db)
        result = tool.run(
            PipelineInput(client=client, engines=[Engine.PERPLEXITY], samples_per_engine=1)
        )
        assert result.ok, result.error
        for record in result.data.records:
            snap = record.citation_history[0]
            assert snap.client_cited is False
            assert snap.client_best_rank is None
            assert snap.competitor_citations == {"coupa.com": 1}
            assert record.verdict is Verdict.KEEP
            assert "visibility gap" in record.verdict_reason


class TestBudgetCeiling:
    def test_audit_stops_early_but_records_are_still_written(
        self, permissive_guardrails, settings, client, engines, db
    ):
        ledger = CostLedger(ceiling_usd=0.05)
        tool = _pipeline(permissive_guardrails, ledger, settings, engines=engines, db=db)
        result = tool.run(PipelineInput(client=client, samples_per_engine=1))
        assert result.ok, result.error
        summary = result.data
        assert any("Budget ceiling" in w for w in summary.warnings)
        assert 0 < summary.engine_calls < 20 * len(Engine)
        assert sum(stub.calls for stub in engines.values()) == summary.engine_calls
        assert len(summary.records) == 20
        assert len(db.prompt_ids(LOB)) == 20
        assert ledger.spent_usd <= 0.05
        # Only the prompts audited before the ceiling carry snapshots.
        with_snapshots = [r for r in summary.records if r.citation_history]
        assert 1 <= len(with_snapshots) < 20
        assert summary.report_path is not None


class TestGuardrails:
    def test_deny_by_default_blocks_before_any_spend(
        self, strict_guardrails, ledger, settings, client, engines, db
    ):
        tool = _pipeline(strict_guardrails, ledger, settings, engines=engines, db=db)
        result = tool.run(PipelineInput(client=client))
        assert result.status is ExecutionStatus.BLOCKED_PENDING_APPROVAL
        assert result.data is None
        assert ledger.spent_usd == pytest.approx(0.0)
        assert all(stub.calls == 0 for stub in engines.values())
        assert db.prompt_ids(LOB) == []


class TestRedirectResolution:
    def test_gemini_redirects_are_attributed_to_the_real_domain(
        self, permissive_guardrails, ledger, settings, client, db
    ):
        redirect = f"https://{GEMINI_REDIRECT_HOST}/grounding-api-redirect/AbC123"
        stuck = f"https://{GEMINI_REDIRECT_HOST}/grounding-api-redirect/unresolvable"
        hostless = f"https://{GEMINI_REDIRECT_HOST}/grounding-api-redirect/hostless"
        citations = [
            Citation(url=redirect, domain=GEMINI_REDIRECT_HOST, position=1, resolved=False),
            Citation(url=stuck, domain=GEMINI_REDIRECT_HOST, position=2, resolved=False),
            Citation(url=hostless, domain=GEMINI_REDIRECT_HOST, position=3, resolved=False),
            Citation(url="https://coupa.com/", domain="coupa.com", position=4),
        ]
        gemini = StubEngine(Engine.GEMINI, citations=citations)
        resolver = FakeResolver()
        tool = _pipeline(
            permissive_guardrails,
            ledger,
            settings,
            engines={Engine.GEMINI: gemini},
            resolver=resolver,
            db=db,
        )
        result = tool.run(
            PipelineInput(
                client=client,
                engines=[Engine.GEMINI],
                samples_per_engine=1,
                resolve_redirects=True,
            )
        )
        assert result.ok, result.error
        assert len(resolver.requested) == 3 * 20
        assert all(GEMINI_REDIRECT_HOST in url for url in resolver.requested)
        for record in result.data.records:
            snap = record.citation_history[0]
            assert snap.cited_domains == ["gep.com", GEMINI_REDIRECT_HOST, "coupa.com"]
            assert snap.client_cited is True
            assert snap.client_best_rank == 1

    def test_redirects_are_left_alone_when_not_requested(
        self, permissive_guardrails, ledger, settings, client, db
    ):
        redirect = f"https://{GEMINI_REDIRECT_HOST}/grounding-api-redirect/AbC123"
        citations = [
            Citation(url=redirect, domain=GEMINI_REDIRECT_HOST, position=1, resolved=False)
        ]
        resolver = FakeResolver()
        tool = _pipeline(
            permissive_guardrails,
            ledger,
            settings,
            engines={Engine.GEMINI: StubEngine(Engine.GEMINI, citations=citations)},
            resolver=resolver,
            db=db,
        )
        result = tool.run(
            PipelineInput(client=client, engines=[Engine.GEMINI], samples_per_engine=1)
        )
        assert result.ok, result.error
        assert resolver.requested == []
        snap = result.data.records[0].citation_history[0]
        assert snap.cited_domains == [GEMINI_REDIRECT_HOST]
        assert snap.client_cited is False


class TestDescribeInvocation:
    def test_projects_engine_spend(self, permissive_guardrails, ledger, settings, client):
        tool = _pipeline(permissive_guardrails, ledger, settings)
        text = tool.describe_invocation(PipelineInput(client=client))
        samples = settings.samples_per_engine
        projected = 20 * samples * _engine_cost(settings, list(Engine))
        assert "20 prompts" in text
        assert f"x {len(Engine)} engines x up to {samples} samples" in text
        assert f"${projected:.2f}" in text
        assert LOB in text
        assert "GEP" in text

    def test_skipping_the_audit_projects_zero(
        self, permissive_guardrails, ledger, settings, client
    ):
        tool = _pipeline(permissive_guardrails, ledger, settings)
        text = tool.describe_invocation(PipelineInput(client=client, skip_engine_audit=True))
        assert "$0.00" in text
        assert "20 prompts" in text


class TestSemrushFailure:
    def test_harvest_failure_falls_back_to_templates(
        self, monkeypatch, permissive_guardrails, ledger, settings, client, db
    ):
        # The retry policy reads the process-wide settings; point it at the
        # hermetic fixture so the failing call is not retried with backoff.
        monkeypatch.setattr("src.core.retry.get_settings", lambda: settings)
        engines = {
            Engine.GOOGLE_AI_OVERVIEW: StubEngine(
                Engine.GOOGLE_AI_OVERVIEW, citations=[], web_triggered=False
            )
        }
        tool = _pipeline(
            permissive_guardrails,
            ledger,
            settings,
            semrush=_semrush(settings, status=500),
            engines=engines,
            db=db,
        )
        result = tool.run(
            PipelineInput(client=client, engines=[Engine.GOOGLE_AI_OVERVIEW], samples_per_engine=1)
        )
        assert result.ok, result.error
        summary = result.data
        assert any("Semrush" in w and SEED in w for w in summary.warnings)
        assert summary.semrush_units == 0
        # 9 branded + 4 non-branded templates, all kept, all selected.
        assert summary.candidates_generated == 13
        assert summary.candidates_kept == 13
        assert summary.prompts_selected == 13
        assert any("Only 9 branded prompts" in w for w in summary.warnings)
        assert any("Only 4 non-branded prompts" in w for w in summary.warnings)
        for record in summary.records:
            assert record.search_volume == 0
            assert record.web_triggers is False
            assert record.verdict is Verdict.DROP
            assert "no search volume" in record.verdict_reason


class TestLazyEngineConstruction:
    def test_missing_engines_are_built_once_from_settings(self, settings):
        tool = PromptTrackerPipeline(settings=settings, engines={})
        first = tool._engine(Engine.CHATGPT_SEARCH)
        assert isinstance(first, OpenAISearchClient)
        assert tool._engine(Engine.CHATGPT_SEARCH) is first
        assert isinstance(tool._engine(Engine.GEMINI), GeminiSearchClient)

    def test_injected_engines_are_reused(self, settings):
        stub = StubEngine(Engine.PERPLEXITY)
        tool = PromptTrackerPipeline(settings=settings, engines={Engine.PERPLEXITY: stub})
        assert tool._engine(Engine.PERPLEXITY) is stub
