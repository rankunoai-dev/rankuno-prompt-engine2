"""CLI tests for subcommands, custom-prompt inputs and scheduling."""

from __future__ import annotations

import json

import pytest

from src.core.schemas import ExecutionStatus, ToolResult
from src.modules.prompt_tracking.__main__ import build_parser, main
from tests.modules.prompt_tracking.test_cli import (
    REQUIRED,
    FakePipeline,
    _summary,
    fake_pipeline,  # noqa: F401 - fixture
)

RUN_FLAGS = REQUIRED[1:]  # without the leading "run"


@pytest.fixture
def jobs_file(tmp_path):
    path = tmp_path / "jobs.json"
    path.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "name": "gep-daily",
                        "interval": "daily",
                        "client": {
                            "brand_name": "GEP",
                            "domains": ["gep.com"],
                            "lob": "Procurement Software",
                            "seed_keywords": ["procurement software"],
                        },
                        "custom_prompts": [{"prompt_text": "What is GEP SMART?"}],
                        "generate_prompts": False,
                    },
                    {
                        "name": "gep-weekly-off",
                        "interval": "weekly",
                        "enabled": False,
                        "client": {
                            "brand_name": "GEP",
                            "domains": ["gep.com"],
                            "lob": "Procurement Software",
                            "seed_keywords": ["procurement software"],
                        },
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


class TestRunInputs:
    def test_legacy_invocation_without_subcommand_still_runs(self, fake_pipeline):  # noqa: F811
        assert main([*RUN_FLAGS, "--approve-spend"]) == 0
        assert fake_pipeline.instances[-1].payload.generate_prompts is True

    def test_custom_prompts_from_flags_and_file(self, fake_pipeline, tmp_path):  # noqa: F811
        path = tmp_path / "p.txt"
        path.write_text(
            "From file one | procurement software | Sub\nFrom file two\n", encoding="utf-8"
        )
        assert (
            main(
                [
                    *REQUIRED,
                    "--approve-spend",
                    "--prompt",
                    "Typed prompt",
                    "--prompts-file",
                    str(path),
                ]
            )
            == 0
        )
        payload = fake_pipeline.instances[-1].payload
        assert [p.prompt_text for p in payload.custom_prompts] == [
            "Typed prompt",
            "From file one",
            "From file two",
        ]
        assert payload.custom_prompts[1].keyword == "procurement software"
        assert payload.generate_prompts is False

    def test_also_generate_keeps_semrush_generation(self, fake_pipeline):  # noqa: F811
        assert main([*REQUIRED, "--approve-spend", "--prompt", "P one", "--also-generate"]) == 0
        assert fake_pipeline.instances[-1].payload.generate_prompts is True

    def test_scale_flags_reach_the_payload(self, fake_pipeline):  # noqa: F811
        argv = [
            *REQUIRED,
            "--approve-spend",
            "--no-adaptive",
            "--max-calls",
            "50",
            "--reuse-hours",
            "12",
            "--workers",
            "8",
        ]
        assert main(argv) == 0
        payload = fake_pipeline.instances[-1].payload
        assert payload.adaptive_sampling is False
        assert payload.max_engine_calls == 50
        assert payload.reuse_within_hours == 12
        assert payload.max_workers == 8

    def test_default_adaptive_is_left_to_settings(self, fake_pipeline):  # noqa: F811
        assert main([*REQUIRED, "--approve-spend"]) == 0
        assert fake_pipeline.instances[-1].payload.adaptive_sampling is None

    def test_text_output_mentions_scale_counters(self, fake_pipeline, capsys):  # noqa: F811
        fake_pipeline.result = fake_pipeline.result.model_copy(
            update={
                "data": _summary(
                    reused_snapshots=4,
                    calls_refused_by_circuit=2,
                    custom_prompts=3,
                    model_shifts=["GEMINI: a -> b"],
                )
            }
        )
        assert main([*REQUIRED, "--approve-spend"]) == 0
        out = capsys.readouterr().out
        assert "reused 4" in out
        assert "refused by circuit 2" in out
        assert "(custom 3)" in out
        assert "MODEL SHIFT: GEMINI: a -> b" in out


class TestScheduleParser:
    def test_subcommands_parse(self, tmp_path):
        jobs = tmp_path / "j.json"
        args = build_parser().parse_args(["schedule", "run-due", "--jobs", str(jobs), "--force"])
        assert args.command == "schedule"
        assert args.schedule_command == "run-due"
        assert args.force is True
        args = build_parser().parse_args(
            ["schedule", "daemon", "--jobs", str(jobs), "--poll-minutes", "5", "--max-cycles", "2"]
        )
        assert args.poll_minutes == 5.0
        assert args.max_cycles == 2
        args = build_parser().parse_args(["schedule", "status", "--jobs", str(jobs), "--json"])
        assert args.json is True

    def test_jobs_is_required(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["schedule", "run-due"])


class TestScheduleCommands:
    def test_status_on_fresh_store(self, fake_pipeline, jobs_file, capsys):  # noqa: F811
        assert main(["schedule", "status", "--jobs", str(jobs_file)]) == 0
        out = capsys.readouterr().out
        assert "gep-daily" in out and "DUE" in out
        assert "gep-weekly-off" in out and "off" in out

        assert main(["schedule", "status", "--jobs", str(jobs_file), "--json"]) == 0
        rows = json.loads(capsys.readouterr().out)
        assert rows[0]["name"] == "gep-daily" and rows[0]["due"] is True
        assert rows[1]["enabled"] is False

    def test_run_due_runs_then_skips(self, fake_pipeline, jobs_file, capsys):  # noqa: F811
        assert main(["schedule", "run-due", "--jobs", str(jobs_file), "--approve-spend"]) == 0
        out = capsys.readouterr().out
        assert "gep-daily: ran (due) status=success" in out
        assert "gep-weekly-off: skipped (disabled)" in out
        assert len(fake_pipeline.instances) == 1
        assert fake_pipeline.instances[0].payload.generate_prompts is False
        assert (
            fake_pipeline.instances[0].payload.custom_prompts[0].prompt_text == "What is GEP SMART?"
        )

        assert main(["schedule", "run-due", "--jobs", str(jobs_file), "--json"]) == 0
        rows = json.loads(capsys.readouterr().out)
        assert rows[0]["ran"] is False and rows[0]["reason"] == "not due"
        assert len(fake_pipeline.instances) == 1

        assert main(["schedule", "run-due", "--jobs", str(jobs_file), "--force"]) == 0
        assert len(fake_pipeline.instances) == 2

    def test_run_due_returns_one_when_a_job_fails(self, fake_pipeline, jobs_file):  # noqa: F811
        FakePipeline.result = ToolResult(
            status=ExecutionStatus.BLOCKED_PENDING_APPROVAL, tool="t", error="denied"
        )
        assert main(["schedule", "run-due", "--jobs", str(jobs_file)]) == 1

    def test_daemon_with_max_cycles(self, fake_pipeline, jobs_file, capsys, monkeypatch):  # noqa: F811
        monkeypatch.setattr("src.modules.prompt_tracking.scheduler.time.sleep", lambda s: None)
        argv = [
            "schedule",
            "daemon",
            "--jobs",
            str(jobs_file),
            "--poll-minutes",
            "0.01",
            "--max-cycles",
            "2",
            "--approve-spend",
        ]
        assert main(argv) == 0
        assert "Daemon finished after 2 cycle(s)." in capsys.readouterr().out
        assert len(fake_pipeline.instances) == 1  # second cycle: not due

    def test_install_task_prints_command_only(self, fake_pipeline, jobs_file, capsys):  # noqa: F811
        assert main(["schedule", "install-task", "--jobs", str(jobs_file)]) == 0
        out = capsys.readouterr().out
        assert out.startswith("schtasks /Create /SC HOURLY")
        assert "schedule run-due" in out
        assert str(jobs_file.resolve()) in out
        assert "UNATTENDED_SPEND_CAP_USD" in out
        assert fake_pipeline.instances == []

        assert main(["schedule", "install-task", "--jobs", str(jobs_file), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["command"].startswith("schtasks")

    def test_budgeted_provider_used_for_unattended_schedule(  # noqa: F811
        self,
        fake_pipeline,  # noqa: F811
        jobs_file,
        settings,
        monkeypatch,  # noqa: F811
    ):
        budgeted = settings.model_copy(update={"unattended_spend_cap_usd": 1.0})
        monkeypatch.setattr("src.modules.prompt_tracking.__main__.get_settings", lambda: budgeted)
        assert main(["schedule", "run-due", "--jobs", str(jobs_file)]) == 0
        provider = type(fake_pipeline.instances[-1].guardrails._provider).__name__
        assert provider == "BudgetedApprovalProvider"
