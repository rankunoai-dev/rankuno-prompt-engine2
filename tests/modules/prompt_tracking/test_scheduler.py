"""Tests for interval parsing, job loading and due-run scheduling."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel

from src.core.schemas import ExecutionStatus, ToolResult
from src.integrations.schemas import Engine
from src.modules.prompt_tracking.scheduler import (
    Scheduler,
    TrackingJob,
    load_jobs,
    parse_interval,
)
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB

NOW = datetime(2026, 9, 16, 9, tzinfo=UTC)
CLIENT = {
    "brand_name": "GEP",
    "domains": ["gep.com"],
    "lob": "Procurement Software",
    "seed_keywords": ["procurement software"],
}


class _Summary(BaseModel):
    run_id: str


def _job(name: str = "gep-daily", **overrides) -> TrackingJob:
    base = {"name": name, "interval": "daily", "client": CLIENT}
    base.update(overrides)
    return TrackingJob.model_validate(base)


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Runner:
    def __init__(self, status: ExecutionStatus = ExecutionStatus.SUCCESS) -> None:
        self.ran: list[str] = []
        self.status = status

    def __call__(self, job: TrackingJob) -> ToolResult[BaseModel]:
        self.ran.append(job.name)
        data = (
            _Summary(run_id=f"run-{len(self.ran)}")
            if self.status is ExecutionStatus.SUCCESS
            else None
        )
        return ToolResult[BaseModel](status=self.status, tool="t", data=data)


@pytest.fixture
def db(tmp_path) -> TimeSeriesDB:
    return TimeSeriesDB(tmp_path / "s.sqlite")


class TestParseInterval:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("daily", timedelta(days=1)),
            ("Weekly", timedelta(weeks=1)),
            ("monthly", timedelta(days=30)),
            ("hourly", timedelta(hours=1)),
            ("15min", timedelta(minutes=15)),
            ("6h", timedelta(hours=6)),
            ("3 d", timedelta(days=3)),
            ("2w", timedelta(weeks=2)),
            ("1mo", timedelta(days=30)),
        ],
    )
    def test_accepted_forms(self, text, expected):
        assert parse_interval(text) == expected

    @pytest.mark.parametrize("text", ["", "fortnightly", "0d", "5x", "h6", "-1d"])
    def test_rejected_forms(self, text):
        with pytest.raises(ValueError, match="interval"):
            parse_interval(text)


class TestTrackingJob:
    def test_interval_validated_and_normalised(self):
        assert _job(interval=" Weekly ").interval == "weekly"
        with pytest.raises(ValueError):
            _job(interval="sometimes")

    def test_name_pattern(self):
        with pytest.raises(ValueError):
            _job(name="bad name!")

    def test_to_pipeline_input_carries_every_setting(self):
        job = _job(
            custom_prompts=[{"prompt_text": "What is GEP SMART?"}],
            generate_prompts=False,
            engines=["PERPLEXITY"],
            samples_per_engine=2,
            track_keyword_rank=False,
            resolve_redirects=True,
            max_engine_calls=50,
            reuse_within_hours=12,
        )
        payload = job.to_pipeline_input()
        assert payload.client.brand_name == "GEP"
        assert [p.prompt_text for p in payload.custom_prompts] == ["What is GEP SMART?"]
        assert payload.generate_prompts is False
        assert payload.engines == [Engine.PERPLEXITY]
        assert payload.samples_per_engine == 2
        assert payload.track_keyword_rank is False
        assert payload.resolve_redirects is True
        assert payload.max_engine_calls == 50
        assert payload.reuse_within_hours == 12


class TestLoadJobs:
    def test_loads_valid_file(self, tmp_path):
        path = tmp_path / "jobs.json"
        path.write_text(
            json.dumps(
                {
                    "jobs": [
                        _job("a").model_dump(mode="json"),
                        _job("b", interval="2w").model_dump(mode="json"),
                    ]
                }
            ),
            encoding="utf-8",
        )
        jobs = load_jobs(path)
        assert [j.name for j in jobs] == ["a", "b"]
        assert jobs[1].every == timedelta(weeks=2)

    @pytest.mark.parametrize("payload", [{}, {"jobs": []}, [], {"jobs": "x"}])
    def test_rejects_missing_or_empty_jobs(self, tmp_path, payload):
        path = tmp_path / "jobs.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError, match="jobs"):
            load_jobs(path)

    def test_rejects_duplicate_names(self, tmp_path):
        path = tmp_path / "jobs.json"
        dumped = _job("dup").model_dump(mode="json")
        path.write_text(json.dumps({"jobs": [dumped, dumped]}), encoding="utf-8")
        with pytest.raises(ValueError, match="duplicate"):
            load_jobs(path)


class TestScheduler:
    def test_never_run_job_is_due_and_state_is_recorded(self, db):
        runner = Runner()
        clock = Clock()
        scheduler = Scheduler(db, runner, clock=clock)
        job = _job()

        assert scheduler.status([job])[0].due is True
        outcomes = scheduler.run_due([job])
        assert runner.ran == ["gep-daily"]
        assert outcomes[0].ran is True
        assert outcomes[0].status == "success"
        assert outcomes[0].run_id == "run-1"
        assert outcomes[0].next_run_at == NOW + timedelta(days=1)

        status = scheduler.status([job])[0]
        assert status.due is False
        assert status.last_run_at == NOW
        assert status.last_run_id == "run-1"
        assert status.last_status == "success"
        assert status.next_run_at == NOW + timedelta(days=1)

    def test_not_due_until_interval_elapses(self, db):
        runner = Runner()
        clock = Clock()
        scheduler = Scheduler(db, runner, clock=clock)
        job = _job(interval="6h")
        scheduler.run_due([job])

        clock.now = NOW + timedelta(hours=5, minutes=59)
        outcomes = scheduler.run_due([job])
        assert outcomes[0].ran is False
        assert outcomes[0].reason == "not due"
        assert outcomes[0].next_run_at == NOW + timedelta(hours=6)

        clock.now = NOW + timedelta(hours=6)
        assert scheduler.run_due([job])[0].ran is True
        assert runner.ran == ["gep-daily", "gep-daily"]

    def test_force_runs_regardless(self, db):
        runner = Runner()
        scheduler = Scheduler(db, runner, clock=Clock())
        job = _job()
        scheduler.run_due([job])
        outcome = scheduler.run_due([job], force=True)[0]
        assert outcome.ran is True
        assert outcome.reason == "forced"
        assert len(runner.ran) == 2

    def test_disabled_jobs_are_skipped(self, db):
        runner = Runner()
        scheduler = Scheduler(db, runner, clock=Clock())
        job = _job(enabled=False)
        outcome = scheduler.run_due([job])[0]
        assert outcome.ran is False
        assert outcome.reason == "disabled"
        assert runner.ran == []
        assert scheduler.status([job])[0].due is False

    def test_failed_run_is_recorded_with_its_status(self, db):
        runner = Runner(ExecutionStatus.BLOCKED_PENDING_APPROVAL)
        scheduler = Scheduler(db, runner, clock=Clock())
        job = _job()
        outcome = scheduler.run_due([job])[0]
        assert outcome.ran is True
        assert outcome.status == "blocked_pending_approval"
        assert outcome.run_id is None
        assert scheduler.status([job])[0].last_status == "blocked_pending_approval"

    def test_daemon_runs_cycles_and_sleeps_between(self, db):
        runner = Runner()
        clock = Clock()
        scheduler = Scheduler(db, runner, clock=clock)
        slept: list[float] = []

        def sleep(seconds: float) -> None:
            slept.append(seconds)
            clock.now += timedelta(days=1)

        cycles = scheduler.daemon([_job()], poll_s=900.0, max_cycles=3, sleep=sleep)
        assert cycles == 3
        assert slept == [900.0, 900.0]
        assert len(runner.ran) == 3
