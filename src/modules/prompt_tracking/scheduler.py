"""Interval scheduling for tracking jobs (daily, weekly, monthly or any interval).

A job file describes what to track and how often. The scheduler is stateless
between invocations: it reads each job's last run from the store, decides
whether the interval has elapsed, runs what is due, and records the outcome.
That makes it safe to invoke from Windows Task Scheduler or cron every hour —
or to run as a long-lived daemon that polls on its own.

Intervals: `hourly`, `daily`, `weekly`, `monthly` (30 days), or `<n>min`,
`<n>h`, `<n>d`, `<n>w`, `<n>mo`.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from pydantic import BaseModel, Field, field_validator

from src.core.logger import get_logger
from src.core.schemas import StrictModel, ToolResult
from src.integrations.schemas import Engine
from src.modules.prompt_tracking.schemas import ClientProfile, CustomPrompt, PipelineInput
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB

__all__ = [
    "JobRunOutcome",
    "JobRunner",
    "JobStatus",
    "Scheduler",
    "TrackingJob",
    "load_jobs",
    "parse_interval",
]

_logger = get_logger("modules.prompt_tracking.scheduler")

_NAMED: Final[dict[str, timedelta]] = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
    "monthly": timedelta(days=30),
}
_UNITS: Final[dict[str, timedelta]] = {
    "min": timedelta(minutes=1),
    "h": timedelta(hours=1),
    "d": timedelta(days=1),
    "w": timedelta(weeks=1),
    "mo": timedelta(days=30),
}
_INTERVAL = re.compile(r"^(\d+)\s*(min|h|d|w|mo)$")


def parse_interval(text: str) -> timedelta:
    """Turn `daily`, `weekly`, `6h`, `3d`, `2w`, `1mo` … into a timedelta."""
    key = text.strip().lower()
    if key in _NAMED:
        return _NAMED[key]
    match = _INTERVAL.match(key)
    if not match or int(match.group(1)) < 1:
        msg = (
            f"Unrecognised interval '{text}'. Use hourly/daily/weekly/monthly or "
            f"<n>min, <n>h, <n>d, <n>w, <n>mo."
        )
        raise ValueError(msg)
    return int(match.group(1)) * _UNITS[match.group(2)]


class TrackingJob(StrictModel):
    """One scheduled tracking configuration."""

    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_.-]+$")
    interval: str = Field(default="daily")
    enabled: bool = True
    client: ClientProfile
    custom_prompts: list[CustomPrompt] = Field(default_factory=list)
    generate_prompts: bool = True
    engines: list[Engine] = Field(default_factory=lambda: list(Engine))
    samples_per_engine: int | None = Field(default=None, ge=1, le=10)
    track_keyword_rank: bool = True
    resolve_redirects: bool = False
    max_engine_calls: int | None = Field(default=None, ge=1)
    reuse_within_hours: int | None = Field(default=None, ge=0)

    @field_validator("interval")
    @classmethod
    def _valid_interval(cls, value: str) -> str:
        """Fail at load time, not at 3 a.m. when the job first fires."""
        parse_interval(value)
        return value.strip().lower()

    @property
    def every(self) -> timedelta:
        """Parsed interval."""
        return parse_interval(self.interval)

    def to_pipeline_input(self) -> PipelineInput:
        """The `PipelineInput` this job runs."""
        return PipelineInput(
            client=self.client,
            engines=self.engines,
            samples_per_engine=self.samples_per_engine,
            resolve_redirects=self.resolve_redirects,
            track_keyword_rank=self.track_keyword_rank,
            custom_prompts=self.custom_prompts,
            generate_prompts=self.generate_prompts,
            max_engine_calls=self.max_engine_calls,
            reuse_within_hours=self.reuse_within_hours,
        )


class JobStatus(StrictModel):
    """Scheduler view of one job."""

    name: str
    interval: str
    enabled: bool
    last_run_at: datetime | None = None
    last_run_id: str | None = None
    last_status: str | None = None
    next_run_at: datetime | None = None
    due: bool


class JobRunOutcome(StrictModel):
    """What `run_due` did for one job."""

    name: str
    ran: bool
    status: str | None = None
    run_id: str | None = None
    next_run_at: datetime | None = None
    reason: str


def load_jobs(path: Path) -> list[TrackingJob]:
    """Load a jobs file: `{"jobs": [ {...}, ... ]}`.

    Names must be unique; a duplicate is a configuration error, not a warning.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(raw_jobs, list) or not raw_jobs:
        msg = f"{path}: expected a non-empty 'jobs' list."
        raise ValueError(msg)
    jobs = [TrackingJob.model_validate(item) for item in raw_jobs]
    names = [j.name for j in jobs]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        msg = f"{path}: duplicate job names {duplicates}."
        raise ValueError(msg)
    return jobs


JobRunner = Callable[[TrackingJob], ToolResult[BaseModel]]
"""Executes one job and returns the tool result."""


class Scheduler:
    """Decides which jobs are due and runs them."""

    def __init__(
        self,
        db: TimeSeriesDB,
        runner: JobRunner,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Build a scheduler.

        Args:
            db: Store holding per-job state.
            runner: Executes a job; injected so tests never build a pipeline.
            clock: UTC time source.
        """
        self._db = db
        self._runner = runner
        self._clock = clock or (lambda: datetime.now(UTC))

    def status(self, jobs: list[TrackingJob]) -> list[JobStatus]:
        """Current state of every job, including whether it is due now."""
        now = self._clock()
        out: list[JobStatus] = []
        for job in jobs:
            state = self._db.job_state(job.name) or {}
            last = _parse(state.get("last_run_at"))
            out.append(
                JobStatus(
                    name=job.name,
                    interval=job.interval,
                    enabled=job.enabled,
                    last_run_at=last,
                    last_run_id=state.get("last_run_id"),
                    last_status=state.get("last_status"),
                    next_run_at=_parse(state.get("next_run_at")),
                    due=job.enabled and self._is_due(job, last, now),
                )
            )
        return out

    def run_due(self, jobs: list[TrackingJob], *, force: bool = False) -> list[JobRunOutcome]:
        """Run every enabled job whose interval has elapsed (or all, with `force`)."""
        outcomes: list[JobRunOutcome] = []
        for job in jobs:
            now = self._clock()
            if not job.enabled:
                outcomes.append(JobRunOutcome(name=job.name, ran=False, reason="disabled"))
                continue
            state = self._db.job_state(job.name) or {}
            last = _parse(state.get("last_run_at"))
            if not force and not self._is_due(job, last, now):
                next_at = (last or now) + job.every
                outcomes.append(
                    JobRunOutcome(name=job.name, ran=False, next_run_at=next_at, reason="not due")
                )
                continue

            _logger.info("job_started", extra={"job": job.name, "interval": job.interval})
            result = self._runner(job)
            run_id = getattr(result.data, "run_id", None) if result.data is not None else None
            next_at = now + job.every
            self._db.record_job_run(
                job.name,
                interval=job.interval,
                last_run_at=now,
                last_run_id=run_id,
                last_status=result.status.value,
                next_run_at=next_at,
            )
            _logger.info(
                "job_finished",
                extra={"job": job.name, "status": result.status.value, "run_id": run_id},
            )
            outcomes.append(
                JobRunOutcome(
                    name=job.name,
                    ran=True,
                    status=result.status.value,
                    run_id=run_id,
                    next_run_at=next_at,
                    reason="forced" if force else "due",
                )
            )
        return outcomes

    def daemon(
        self,
        jobs: list[TrackingJob],
        *,
        poll_s: float,
        max_cycles: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> int:
        """Poll forever (or `max_cycles` times), running due jobs each cycle.

        Returns the number of cycles completed. Interrupting with Ctrl-C ends
        the loop cleanly after the current job.
        """
        cycles = 0
        try:
            while max_cycles is None or cycles < max_cycles:
                ran = [o for o in self.run_due(jobs) if o.ran]
                cycles += 1
                _logger.info("daemon_cycle", extra={"cycle": cycles, "ran": [o.name for o in ran]})
                if max_cycles is not None and cycles >= max_cycles:
                    break
                sleep(poll_s)
        except KeyboardInterrupt:  # pragma: no cover - operator action
            _logger.info("daemon_stopped", extra={"cycles": cycles})
        return cycles

    @staticmethod
    def _is_due(job: TrackingJob, last: datetime | None, now: datetime) -> bool:
        """Due when never run or when the interval has elapsed since the last run."""
        return last is None or now >= last + job.every


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
