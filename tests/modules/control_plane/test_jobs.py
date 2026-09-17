"""JobManager: queueing, dedupe, progress folding, failure handling, worker thread."""

from __future__ import annotations

import threading
import time

import pytest

from src.core.schemas import ExecutionStatus
from src.integrations.schemas import Engine
from src.modules.control_plane.jobs import JobManager
from src.modules.control_plane.runner import ProjectRunner
from src.modules.control_plane.schemas import JobState, ProjectCreate, RunRequest
from tests.modules.control_plane.conftest import CLIENT, NOW
from tests.modules.control_plane.test_runner import FakePipeline


@pytest.fixture
def runner(store, db, settings):
    return ProjectRunner(
        store, db, settings=settings, pipeline=FakePipeline(db=db), clock=lambda: NOW
    )


@pytest.fixture
def manager(runner, store):
    return JobManager(runner, store, autostart=False, clock=lambda: NOW)


def test_submit_queues_and_get_reports_position(manager, project, prompts):
    first = manager.submit(project.id)
    assert first.state is JobState.QUEUED and first.position == 0
    assert first.project_name == project.name
    assert first.progress.checks_total == 0 and first.progress.percent == 0.0
    second = manager.submit(project.id, RunRequest(force=True))
    assert second.id != first.id and second.position == 1
    assert manager.get(second.id).position == 1
    assert [j.id for j in manager.active()] == [first.id, second.id]


def test_identical_active_request_is_deduplicated(manager, project, prompts):
    a = manager.submit(project.id, RunRequest(force=True))
    b = manager.submit(project.id, RunRequest(force=True))
    assert a.id == b.id
    c = manager.submit(project.id, RunRequest(force=True, engines=[Engine.GEMINI]))
    assert c.id != a.id
    manager.run_pending()
    d = manager.submit(project.id, RunRequest(force=True))  # finished jobs do not dedupe
    assert d.id != a.id


def test_run_pending_executes_and_folds_progress(manager, project, prompts):
    job = manager.submit(project.id)
    assert manager.run_pending() == 1
    done = manager.get(job.id)
    assert done.state is JobState.FINISHED
    assert done.started_at == NOW and done.finished_at == NOW
    assert done.outcome is not None and done.outcome.batches == 1 and done.outcome.prompts_run == 2
    assert done.progress.phase == "done" and done.progress.percent == 100.0
    assert done.progress.checks_total == 2 * len(Engine)
    assert done.progress.checks_done == done.progress.checks_total
    assert done.progress.batches_done == done.progress.batches_total == 1
    assert done.progress.engine_calls == 2 * len(Engine)
    assert done.error is None
    assert manager.run_pending() == 0


def test_nothing_due_is_a_finished_job_with_reason(manager, project, prompts):
    manager.submit(project.id)
    manager.run_pending()
    job = manager.submit(project.id)
    manager.run_pending()
    done = manager.get(job.id)
    assert done.state is JobState.FINISHED
    assert done.outcome is not None and done.outcome.reason == "nothing due"
    assert done.progress.message == "nothing due"


def test_runner_exception_marks_job_failed(store, db, settings, project, prompts):
    class Exploding(ProjectRunner):
        def run(self, project_id, request=None, progress=None):
            raise RuntimeError("vendor melted")

    runner = Exploding(store, db, settings=settings, pipeline=FakePipeline(db=db))
    manager = JobManager(runner, store, autostart=False, clock=lambda: NOW)
    job = manager.submit(project.id)
    manager.run_pending()
    failed = manager.get(job.id)
    assert failed.state is JobState.FAILED
    assert failed.error == "RuntimeError: vendor melted"
    assert failed.progress.phase == "failed" and failed.progress.message == "vendor melted"
    assert failed.finished_at == NOW
    assert manager.active() == []


def test_blocked_pipeline_still_finishes_the_job(store, db, settings, project, prompts):
    fake = FakePipeline(status=ExecutionStatus.BLOCKED_PENDING_APPROVAL)
    runner = ProjectRunner(store, db, settings=settings, pipeline=fake, clock=lambda: NOW)
    manager = JobManager(runner, store, autostart=False)
    job = manager.submit(project.id)
    manager.run_pending()
    done = manager.get(job.id)
    assert done.state is JobState.FINISHED
    assert done.outcome is not None and done.outcome.statuses == ["blocked_pending_approval"]


def test_list_orders_active_first_then_newest_and_filters(manager, store, project, prompts):
    other = store.create_project(ProjectCreate(name="other", client=CLIENT))
    a = manager.submit(project.id)
    manager.run_pending()
    b = manager.submit(other.id, RunRequest(force=True))
    c = manager.submit(project.id, RunRequest(force=True))
    ids = [j.id for j in manager.list_jobs()]
    assert ids == [c.id, b.id, a.id]  # active (newest first), then finished
    assert [j.id for j in manager.list_jobs(project.id)] == [c.id, a.id]
    assert [j.id for j in manager.list_jobs(limit=1)] == [c.id]


def test_unknown_project_and_job_raise_key_error(manager):
    with pytest.raises(KeyError):
        manager.submit("nope-nope-nope")
    with pytest.raises(KeyError):
        manager.get("missing-job-id")


def test_run_due_all_queues_enabled_projects_once(manager, store, project, prompts):
    store.create_project(ProjectCreate(name="paused", client=CLIENT, enabled=False))
    jobs = manager.run_due_all()
    assert [j.project_id for j in jobs] == [project.id]
    again = manager.run_due_all()
    assert [j.id for j in again] == [jobs[0].id]  # deduped while still queued


def test_trim_keeps_only_recent_finished_jobs(runner, store, project, prompts):
    manager = JobManager(runner, store, autostart=False, keep=2, clock=lambda: NOW)
    ids = []
    for _ in range(4):
        ids.append(manager.submit(project.id, RunRequest(force=True)).id)
        manager.run_pending()
    remaining = [j.id for j in manager.list_jobs()]
    assert remaining == [ids[3], ids[2]]
    with pytest.raises(KeyError):
        manager.get(ids[0])


def test_execute_ignores_unknown_or_already_started_jobs(manager, project, prompts):
    manager._execute("ghost")  # no such job: no error
    job = manager.submit(project.id)
    manager.run_pending()
    manager._execute(job.id)  # already finished: untouched
    assert manager.get(job.id).state is JobState.FINISHED


def test_worker_thread_runs_submitted_job(runner, store, project, prompts):
    manager = JobManager(runner, store, autostart=True)
    job = manager.submit(project.id)
    deadline = time.monotonic() + 10
    while manager.get(job.id).state.active and time.monotonic() < deadline:
        time.sleep(0.02)
    finished = manager.get(job.id)
    assert finished.state is JobState.FINISHED, finished
    assert manager._worker is not None and manager._worker.daemon
    # A second submit reuses the live worker.
    before = manager._worker
    manager.submit(project.id, RunRequest(force=True))
    assert manager._worker is before
    assert isinstance(before, threading.Thread)
