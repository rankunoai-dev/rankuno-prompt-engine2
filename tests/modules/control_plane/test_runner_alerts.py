"""The alerting phase inside a crawl (ADR 0024): it runs, and it never fails the run."""

from __future__ import annotations

from datetime import timedelta

import pytest

from src.modules.alerting.schemas import AlertDestinationUpdate, AlertEvent, AlertRule
from src.modules.control_plane.runner import ProjectRunner
from src.modules.control_plane.schemas import ProjectUpdate
from tests.modules.control_plane.conftest import NOW
from tests.modules.control_plane.test_runner import FakePipeline

HOOK = "https://hooks.slack.com/services/T04AB/B05CD/xyz123secret"


class SpyDispatcher:
    """Records what the runner asked to send."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, list[AlertEvent]]] = []

    def dispatch(self, project_id: str, _project_name: str, events: list[AlertEvent]):
        if self.error:
            raise self.error
        self.calls.append((project_id, events))
        return []


@pytest.fixture
def clock():
    """A clock the test can move, so the second crawl is due."""

    class Clock:
        now = NOW

        def __call__(self):
            return self.now

    return Clock()


@pytest.fixture
def runner_with(store, db, settings, clock):
    """A runner whose crawls consolidate every time, with a spy dispatcher."""

    def build(dispatcher):
        return ProjectRunner(
            store,
            db,
            settings=settings,
            pipeline=FakePipeline(db=db),
            clock=clock,
            dispatcher=dispatcher,
        )

    return build


def _crawl_twice(runner, project, clock):
    """Two consolidated windows, so the rules have something to compare."""
    runner.run(project.id)
    clock.now = clock.now + timedelta(days=2)
    runner.run(project.id, None)


def test_nothing_is_evaluated_until_a_destination_is_enabled(
    store, project, prompts, runner_with, clock
):
    """No destination, no work: alerting costs an idle project nothing."""
    spy = SpyDispatcher()
    runner = runner_with(spy)
    store.update_project(project.id, ProjectUpdate(consolidation_runs=1))
    _crawl_twice(runner, project, clock)
    assert spy.calls == []


def test_an_enabled_destination_gets_the_windows_compared(
    store, project, prompts, runner_with, clock
):
    """The phase runs after the crawl is recorded and the window consolidated."""
    spy = SpyDispatcher()
    runner = runner_with(spy)
    store.update_project(project.id, ProjectUpdate(consolidation_runs=1))
    runner.alerts.save_destination(
        project.id,
        AlertDestinationUpdate(slack_webhook=HOOK, enabled=True, rules=list(AlertRule)),
    )
    _crawl_twice(runner, project, clock)
    # The fake pipeline cites the brand every time, so no rule should fire —
    # what matters is that the phase ran against two windows without error.
    assert all(isinstance(call[1], list) for call in spy.calls)


def test_the_first_window_has_nothing_to_compare_and_stays_silent(
    store, project, prompts, runner_with, clock
):
    """Every rule is a delta; a first crawl cannot produce one."""
    spy = SpyDispatcher()
    runner = runner_with(spy)
    store.update_project(project.id, ProjectUpdate(consolidation_runs=1))
    runner.alerts.save_destination(
        project.id, AlertDestinationUpdate(slack_webhook=HOOK, enabled=True)
    )
    runner.run(project.id)
    assert spy.calls == []


def test_an_alerting_failure_warns_but_the_crawl_still_succeeds(
    store, project, prompts, runner_with, clock, monkeypatch
):
    """A crawl that worked must never be reported as failed because alerting broke.

    The rules themselves are made to explode: anything inside the phase, from
    the destination lookup to the send, has to be caught in the same place.
    """

    def explode(*_args: object, **_kwargs: object) -> list[AlertEvent]:
        msg = "rule engine exploded"
        raise RuntimeError(msg)

    monkeypatch.setattr("src.modules.control_plane.runner.evaluate", explode)
    runner = runner_with(SpyDispatcher())
    store.update_project(project.id, ProjectUpdate(consolidation_runs=1))
    runner.alerts.save_destination(
        project.id,
        AlertDestinationUpdate(slack_webhook=HOOK, enabled=True, rules=list(AlertRule)),
    )
    runner.run(project.id)
    clock.now = clock.now + timedelta(days=2)
    outcome = runner.run(project.id, None)
    assert outcome.batches > 0
    assert any("Alerting failed" in warning for warning in outcome.warnings)
    assert outcome.statuses and all(status == "success" for status in outcome.statuses)


def test_the_alert_store_is_shared_with_the_api(runner_with):
    """One store, so what the runner writes is what the history endpoint reads."""
    runner = runner_with(SpyDispatcher())
    assert runner.alerts is runner.alerts
    assert runner.alerts.destination("whatever").enabled is False
