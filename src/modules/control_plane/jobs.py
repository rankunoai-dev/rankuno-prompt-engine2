"""Background run queue with live progress.

A `RunJob` is created the moment the UI (or the poller) asks for a run; the HTTP
request returns at once with the job. One worker thread executes jobs in order,
so two runs never compete for the ledger, the SQLite file or vendor rate
buckets. Progress arrives from `ProjectRunner.run(..., progress=…)` and is
readable at any time via `get()`; the page polls it to draw the bar and to
notify when the job finishes.

Jobs live in memory (the last `keep` are retained). Durable run history is the
`runs` table written by the pipeline; a restart forgets queue state only.
"""

from __future__ import annotations

import queue
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from src.core.logger import get_logger
from src.integrations.usage import usage_context
from src.modules.control_plane.runner import ProjectRunner
from src.modules.control_plane.schemas import JobState, RunJob, RunProgress, RunRequest
from src.modules.control_plane.store import ProjectStore

__all__ = ["JobManager", "QueueFull"]

_logger = get_logger("modules.control_plane.jobs")


class QueueFull(RuntimeError):
    """Raised by `submit` when the waiting queue is at its bound."""

    def __init__(self, waiting: int, limit: int) -> None:
        """Record how full the queue was."""
        super().__init__(f"{waiting} run(s) already queued; the limit is {limit}. Retry later.")
        self.waiting = waiting
        self.limit = limit


class JobManager:
    """Thread-safe queue of project runs executed by one background worker."""

    def __init__(
        self,
        runner: ProjectRunner,
        store: ProjectStore,
        *,
        autostart: bool = True,
        keep: int = 200,
        max_queued: int = 50,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Build a manager.

        Args:
            runner: Executes runs; its `run()` accepts a progress sink.
            store: Used to validate projects and label jobs.
            autostart: Start the worker thread on first submit. Tests pass False
                and drive `run_pending()` themselves.
            keep: How many finished jobs to retain in memory.
            max_queued: Jobs allowed to wait for the single worker. Beyond it,
                `submit` raises `QueueFull` (HTTP 429) — queued jobs are held in
                memory and were previously unbounded.
            clock: UTC time source.
        """
        self._runner = runner
        self._store = store
        self._autostart = autostart
        self._keep = max(keep, 1)
        self._max_queued = max(max_queued, 1)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._jobs: dict[str, RunJob] = {}
        self._order: list[str] = []
        self._queue: queue.Queue[str] = queue.Queue()
        self._worker: threading.Thread | None = None

    # -- submitting --------------------------------------------------------------

    def submit(self, project_id: str, request: RunRequest | None = None) -> RunJob:
        """Queue a run. An identical request already queued or running is returned instead.

        Raises:
            KeyError: Unknown project (the API maps this to 404).
            QueueFull: Too many jobs already waiting (the API maps this to 429).
        """
        request = request or RunRequest()
        project = self._store.get_project(project_id)
        with self._lock:
            waiting = 0
            for job_id in self._order:
                job = self._jobs[job_id]
                if job.project_id == project_id and job.state.active and job.request == request:
                    return self._snapshot(job)
                waiting += job.state is JobState.QUEUED
            if waiting >= self._max_queued:
                raise QueueFull(waiting, self._max_queued)
            job = RunJob(
                id=uuid.uuid4().hex[:16],
                project_id=project_id,
                project_name=project.name,
                request=request,
                created_at=self._clock(),
                progress=RunProgress(message="Waiting for the worker"),
            )
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._trim()
            snapshot = self._snapshot(job)
        self._queue.put(job.id)
        if self._autostart:
            self._ensure_worker()
        _logger.info("run_job_queued", extra={"job_id": job.id, "project_id": project_id})
        return snapshot

    def run_due_all(self) -> list[RunJob]:
        """Queue a due-work run for every enabled project (poller entry point)."""
        return [self.submit(p.id) for p in self._store.list_projects() if p.enabled]

    # -- reading -----------------------------------------------------------------

    def get(self, job_id: str) -> RunJob:
        """A copy of one job with its queue position.

        Raises:
            KeyError: Unknown job id.
        """
        with self._lock:
            return self._snapshot(self._jobs[job_id])

    def list_jobs(self, project_id: str | None = None, *, limit: int = 50) -> list[RunJob]:
        """Jobs newest first, active ones before finished ones, optionally for one project."""
        with self._lock:
            jobs = [self._jobs[j] for j in reversed(self._order)]
            if project_id is not None:
                jobs = [j for j in jobs if j.project_id == project_id]
            jobs.sort(key=lambda j: not j.state.active)
            return [self._snapshot(j) for j in jobs[:limit]]

    def active(self) -> list[RunJob]:
        """Queued and running jobs, oldest first."""
        with self._lock:
            return [
                self._snapshot(self._jobs[j]) for j in self._order if self._jobs[j].state.active
            ]

    # -- executing ---------------------------------------------------------------

    def run_pending(self) -> int:
        """Execute every queued job on the calling thread. Returns how many ran."""
        ran = 0
        while True:
            try:
                job_id = self._queue.get_nowait()
            except queue.Empty:
                return ran
            self._execute(job_id)
            ran += 1

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._work_forever, name="control-plane-runs", daemon=True
            )
            self._worker.start()

    def _work_forever(self) -> None:
        while True:
            job_id = self._queue.get()
            self._execute(job_id)

    def _execute(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state is not JobState.QUEUED:
                return
            job.state = JobState.RUNNING
            job.started_at = self._clock()
            job.progress = RunProgress(
                phase="planning", message="Starting", updated_at=job.started_at
            )

        def sink(progress: RunProgress) -> None:
            with self._lock:
                job.progress = progress

        try:
            with usage_context(source="control_plane"):
                outcome = self._runner.run(job.project_id, job.request, progress=sink)
        except Exception as exc:  # noqa: BLE001 - the worker must survive one bad run
            _logger.exception("run_job_failed", extra={"job_id": job_id})
            with self._lock:
                job.state = JobState.FAILED
                job.error = f"{type(exc).__name__}: {exc}"
                job.finished_at = self._clock()
                job.progress = job.progress.model_copy(
                    update={"phase": "failed", "message": str(exc)}
                )
                self._trim()
            return
        with self._lock:
            job.state = JobState.FINISHED
            job.outcome = outcome
            job.finished_at = self._clock()
            job.progress = job.progress.model_copy(
                update={
                    "phase": "done",
                    "percent": 100.0,
                    "checks_done": job.progress.checks_total,
                    "message": outcome.reason or f"Ran {outcome.prompts_run} prompt(s)",
                }
            )
            self._trim()
        _logger.info(
            "run_job_finished",
            extra={"job_id": job_id, "batches": outcome.batches, "statuses": outcome.statuses},
        )

    # -- helpers -----------------------------------------------------------------

    def _snapshot(self, job: RunJob) -> RunJob:
        """Copy with the queue position filled in (call under the lock)."""
        position = 0
        if job.state is JobState.QUEUED:
            position = sum(
                1
                for j in self._order[: self._order.index(job.id)]
                if self._jobs[j].state is JobState.QUEUED
            )
        return job.model_copy(update={"position": position}, deep=True)

    def _trim(self) -> None:
        """Forget the oldest finished jobs beyond `keep` (call under the lock)."""
        finished = [j for j in self._order if not self._jobs[j].state.active]
        for job_id in finished[: max(0, len(finished) - self._keep)]:
            self._order.remove(job_id)
            del self._jobs[job_id]
