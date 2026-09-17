"""Tests for the `python -m src.modules.prompt_tracking` entry point.

The pipeline is replaced by a fake so these tests exercise argument parsing,
approval wiring and output formatting only.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import ClassVar

import pytest
from pydantic import BaseModel

from src.core.guardrails import (
    BudgetedApprovalProvider,
    CallbackApprovalProvider,
    DenyByDefaultProvider,
)
from src.core.schemas import ExecutionStatus, ToolResult
from src.integrations.schemas import Engine
from src.modules.prompt_tracking.__main__ import build_parser, main
from src.modules.prompt_tracking.pipeline import PromptTrackerPipeline
from src.modules.prompt_tracking.schemas import TrackerRunSummary

TOOL_NAME = PromptTrackerPipeline.metadata.name
REQUIRED = ["run", "--lob", "Procurement Software", "--brand", "GEP", "--domain", "gep.com"]
REQUIRED += ["--keyword", "procurement software"]


def _summary(**overrides) -> TrackerRunSummary:
    now = datetime(2026, 9, 16, tzinfo=UTC)
    base = {
        "run_id": "run0123456789abcd",
        "lob": "Procurement Software",
        "brand_name": "GEP",
        "started_at": now,
        "finished_at": now,
        "candidates_generated": 40,
        "candidates_kept": 30,
        "prompts_selected": 20,
        "engine_calls": 240,
        "failed_engine_calls": 2,
        "estimated_cost_usd": 4.5,
        "semrush_units": 530,
        "report_path": "reports/master.csv",
        "warnings": ["Only 9 branded prompts passed the intent gate; 10 required."],
    }
    return TrackerRunSummary(**{**base, **overrides})


def _success() -> ToolResult[TrackerRunSummary]:
    return ToolResult[TrackerRunSummary](
        status=ExecutionStatus.SUCCESS, tool=TOOL_NAME, data=_summary()
    )


class FakePipeline:
    """Captures constructor arguments and the payload passed to `run`."""

    instances: ClassVar[list[FakePipeline]] = []
    result: ClassVar[ToolResult] = _success()

    def __init__(self, guardrails=None, cost_ledger=None, *, settings=None, **_) -> None:
        self.guardrails = guardrails
        self.cost_ledger = cost_ledger
        self.settings = settings
        self.payload = None
        type(self).instances.append(self)

    def run(self, payload):
        self.payload = payload
        return type(self).result


@pytest.fixture
def fake_pipeline(monkeypatch, settings) -> type[FakePipeline]:
    FakePipeline.instances = []
    FakePipeline.result = _success()
    monkeypatch.setattr("src.modules.prompt_tracking.__main__.PromptTrackerPipeline", FakePipeline)
    monkeypatch.setattr("src.modules.prompt_tracking.__main__.get_settings", lambda: settings)
    return FakePipeline


def _provider_name(pipeline: FakePipeline) -> str:
    return type(pipeline.guardrails._provider).__name__


class TestParser:
    @pytest.mark.parametrize("missing", ["--lob", "--brand", "--domain", "--keyword"])
    def test_required_arguments(self, missing, capsys):
        args = []
        skip = False
        for token in REQUIRED:
            if token == missing:
                skip = True
                continue
            if skip:
                skip = False
                continue
            args.append(token)
        with pytest.raises(SystemExit) as exc:
            build_parser().parse_args(args)
        assert exc.value.code == 2
        assert missing in capsys.readouterr().err

    def test_full_parse(self):
        args = build_parser().parse_args(REQUIRED)
        assert args.lob == "Procurement Software"
        assert args.brand == "GEP"
        assert args.domain == ["gep.com"]
        assert args.keyword == ["procurement software"]
        assert args.alias == []
        assert args.engine is None
        assert args.samples is None
        assert args.landing_pages is None
        assert args.approve_spend is False
        assert args.json is False

    def test_engine_choices(self, capsys):
        args = build_parser().parse_args(
            [*REQUIRED, "--engine", "GEMINI", "--engine", "PERPLEXITY"]
        )
        assert args.engine == ["GEMINI", "PERPLEXITY"]
        with pytest.raises(SystemExit):
            build_parser().parse_args([*REQUIRED, "--engine", "BING"])
        assert "invalid choice" in capsys.readouterr().err

    def test_every_engine_value_is_a_valid_choice(self):
        for engine in Engine:
            args = build_parser().parse_args([*REQUIRED, "--engine", engine.value])
            assert args.engine == [engine.value]


class TestMain:
    def test_success_prints_summary_and_returns_zero(self, fake_pipeline, capsys):
        assert main([*REQUIRED, "--approve-spend"]) == 0
        out = capsys.readouterr().out
        assert "Prompts selected: 20" in out
        assert "run0123456789abcd" in out
        assert "Engine calls: 240 (failed 2, reused 0, refused by circuit 0)" in out
        assert "Estimated spend: $4.50" in out
        assert "Report: reports/master.csv" in out
        assert "WARNING: Only 9 branded prompts" in out

    def test_json_output_contains_run_id(self, fake_pipeline, capsys):
        assert main([*REQUIRED, "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "success"
        assert payload["data"]["run_id"] == "run0123456789abcd"
        assert payload["data"]["prompts_selected"] == 20

    @pytest.mark.parametrize(
        "status", [ExecutionStatus.FAILED, ExecutionStatus.BLOCKED_PENDING_APPROVAL]
    )
    def test_failure_returns_one_and_prints_status_json(self, fake_pipeline, capsys, status):
        fake_pipeline.result = ToolResult[TrackerRunSummary](
            status=status, tool=TOOL_NAME, error="boom"
        )
        assert main(REQUIRED) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload == {"status": status.value, "error": "boom"}

    def test_success_without_data_falls_through_to_json(self, fake_pipeline, capsys):
        fake_pipeline.result = ToolResult[TrackerRunSummary](
            status=ExecutionStatus.SUCCESS, tool=TOOL_NAME
        )
        assert main(REQUIRED) == 0
        assert json.loads(capsys.readouterr().out)["status"] == "success"

    def test_report_not_written_message(self, fake_pipeline, capsys):
        fake_pipeline.result = ToolResult[TrackerRunSummary](
            status=ExecutionStatus.SUCCESS,
            tool=TOOL_NAME,
            data=_summary(report_path=None, warnings=[]),
        )
        assert main(REQUIRED) == 0
        out = capsys.readouterr().out
        assert "Report: not written" in out
        assert "WARNING" not in out

    def test_json_output_from_real_result_shape(self, fake_pipeline, capsys):
        fake_pipeline.result = ToolResult[BaseModel](
            status=ExecutionStatus.SUCCESS, tool=TOOL_NAME, data=_summary()
        )
        main([*REQUIRED, "--json"])
        assert "run_id" in json.loads(capsys.readouterr().out)["data"]


class TestApprovalWiring:
    def test_approve_spend_uses_callback_provider(self, fake_pipeline):
        main([*REQUIRED, "--approve-spend"])
        (pipeline,) = fake_pipeline.instances
        assert _provider_name(pipeline) == "CallbackApprovalProvider"
        provider = pipeline.guardrails._provider
        assert isinstance(provider, CallbackApprovalProvider)
        assert provider.request_approval(PromptTrackerPipeline.metadata, "ctx") is True

    def test_unattended_cap_uses_budgeted_provider(self, fake_pipeline, monkeypatch, settings):
        capped = settings.model_copy(update={"unattended_spend_cap_usd": 0.5})
        monkeypatch.setattr("src.modules.prompt_tracking.__main__.get_settings", lambda: capped)
        main(REQUIRED)
        (pipeline,) = fake_pipeline.instances
        assert _provider_name(pipeline) == "BudgetedApprovalProvider"
        assert isinstance(pipeline.guardrails._provider, BudgetedApprovalProvider)
        assert pipeline.settings is capped

    def test_default_is_deny_by_default(self, fake_pipeline, settings):
        main(REQUIRED)
        (pipeline,) = fake_pipeline.instances
        assert _provider_name(pipeline) == "DenyByDefaultProvider"
        assert isinstance(pipeline.guardrails._provider, DenyByDefaultProvider)
        assert pipeline.settings is settings
        assert pipeline.cost_ledger is not None

    def test_approve_spend_wins_over_unattended_cap(self, fake_pipeline, monkeypatch, settings):
        capped = settings.model_copy(update={"unattended_spend_cap_usd": 0.5})
        monkeypatch.setattr("src.modules.prompt_tracking.__main__.get_settings", lambda: capped)
        main([*REQUIRED, "--approve-spend"])
        assert _provider_name(fake_pipeline.instances[0]) == "CallbackApprovalProvider"


class TestPayload:
    def test_landing_pages_file_is_read_into_client(self, fake_pipeline, tmp_path):
        pages = tmp_path / "pages.txt"
        pages.write_text(
            "https://www.gep.com/software/procurement-software\n\n  \n"
            "  https://www.gep.com/about-us  \n",
            encoding="utf-8",
        )
        main([*REQUIRED, "--landing-pages", str(pages)])
        payload = fake_pipeline.instances[0].payload
        assert payload.client.landing_pages == [
            "https://www.gep.com/software/procurement-software",
            "https://www.gep.com/about-us",
        ]

    def test_all_options_flow_into_payload(self, fake_pipeline):
        main(
            [
                *REQUIRED,
                "--alias",
                "GEP SMART",
                "--competitor",
                "coupa.com",
                "--subtopic",
                "Source to Pay",
                "--domain",
                "nexxe.com",
                "--engine",
                "GEMINI",
                "--engine",
                "PERPLEXITY",
                "--samples",
                "2",
                "--resolve-redirects",
                "--skip-engine-audit",
            ]
        )
        payload = fake_pipeline.instances[0].payload
        assert payload.client.brand_name == "GEP"
        assert payload.client.aliases == ["GEP SMART"]
        assert payload.client.domains == ["gep.com", "nexxe.com"]
        assert payload.client.competitor_domains == ["coupa.com"]
        assert payload.client.subtopics == ["Source to Pay"]
        assert payload.client.landing_pages == []
        assert payload.engines == [Engine.GEMINI, Engine.PERPLEXITY]
        assert payload.samples_per_engine == 2
        assert payload.resolve_redirects is True
        assert payload.skip_engine_audit is True

    def test_defaults_track_every_engine(self, fake_pipeline):
        main(REQUIRED)
        payload = fake_pipeline.instances[0].payload
        assert payload.engines == list(Engine)
        assert payload.samples_per_engine is None
        assert payload.resolve_redirects is False
        assert payload.skip_engine_audit is False


class TestCosts:
    def test_costs_text_and_json(self, monkeypatch, settings, capsys):
        from src.integrations.usage import ApiCall, get_usage_ledger

        monkeypatch.setattr("src.modules.prompt_tracking.__main__.get_settings", lambda: settings)
        get_usage_ledger(settings).record(
            ApiCall(
                vendor="serpapi",
                operation="google_search",
                source="cli",
                run_id="r9",
                estimated_cost_usd=0.01,
                modelled_cost_usd=0.01,
                search_calls=1,
            )
        )
        assert main(["costs"]) == 0
        out = capsys.readouterr().out
        assert "Usage ledger: 1 calls" in out and "serpapi" in out
        assert "cost_serpapi_call_usd: current 0.01 -> suggested 0.0115" in out
        assert main(["costs", "--json", "--run-id", "r9", "--days", "1"]) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["calls"] == 1 and report["runs"][0]["run_id"] == "r9"
        assert main(["costs", "--run-id", "none"]) == 0
        assert "Usage ledger: 0 calls" in capsys.readouterr().out
