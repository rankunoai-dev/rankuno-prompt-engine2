"""Server wiring: parser, runner construction, approval mode, poller loop, job manager."""

from __future__ import annotations

import threading

from src.core.guardrails import (
    BudgetedApprovalProvider,
    CallbackApprovalProvider,
    DenyByDefaultProvider,
)
from src.modules.control_plane.__main__ import build_parser, build_runner, main, poll_forever
from src.modules.control_plane.jobs import JobManager


def test_parser_defaults(settings):
    """Host and port are unset on the CLI so `HOST` / `PORT` settings (PaaS-injected) win."""
    args = build_parser().parse_args([])
    assert args.host is None and args.port is None
    assert (args.host or settings.host, args.port or settings.port) == ("127.0.0.1", 8787)
    assert args.poll_minutes == 0.0 and args.approve_spend is False
    args = build_parser().parse_args(["--port", "9000", "--poll-minutes", "15", "--approve-spend"])
    assert args.port == 9000 and args.poll_minutes == 15.0 and args.approve_spend is True


def test_build_runner_approval_modes(settings):
    assert isinstance(
        build_runner(settings, approve=True)._guardrails._provider, CallbackApprovalProvider
    )
    assert isinstance(
        build_runner(settings, approve=False)._guardrails._provider, DenyByDefaultProvider
    )
    budgeted = settings.model_copy(update={"unattended_spend_cap_usd": 1.0})
    assert isinstance(
        build_runner(budgeted, approve=False)._guardrails._provider, BudgetedApprovalProvider
    )


def test_poll_forever_runs_cycles_and_survives_errors():
    calls = []

    class Runner:
        def run_due_all(self):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("boom")
            return []

    slept = []
    cycles = poll_forever(Runner(), 5.0, stop=threading.Event(), sleep=slept.append, max_cycles=3)
    assert cycles == 3 and len(calls) == 3 and slept == [5.0, 5.0]

    stop = threading.Event()
    stop.set()
    assert poll_forever(Runner(), 1.0, stop=stop, sleep=slept.append) == 0


def test_poll_forever_skip_first_waits_a_cycle_before_the_first_run() -> None:
    """skip_first delays the first cycle so a redeploy never fires a crawl at boot."""
    order: list[tuple[str, int]] = []
    sleeps: list[float] = []

    class Runner:
        def run_due_all(self) -> list[object]:
            order.append(("run", len(sleeps)))  # how many sleeps preceded this run
            return []

    poll_forever(
        Runner(), 5.0, stop=threading.Event(), sleep=sleeps.append, max_cycles=1, skip_first=True
    )
    # The one run happened only after the boot-delay sleep (len==1), not at startup.
    assert order == [("run", 1)]


def test_main_starts_uvicorn_with_app_and_queues_poller(monkeypatch, settings):
    captured = {}

    def fake_run(app, host, port, log_level):
        captured.update(app=app, host=host, port=port)

    monkeypatch.setattr("src.modules.control_plane.__main__.uvicorn.run", fake_run)
    monkeypatch.setattr("src.modules.control_plane.__main__.get_settings", lambda: settings)
    started = []

    def fake_thread(**kw):
        started.append(kw)
        return type("T", (), {"start": lambda self: None})()

    monkeypatch.setattr("src.modules.control_plane.__main__.threading.Thread", fake_thread)
    assert main(["--port", "8790", "--poll-minutes", "1"]) == 0
    assert captured["port"] == 8790 and captured["host"] == "127.0.0.1"
    assert captured["app"].title.startswith("RankUno")
    assert [kw["name"] for kw in started] == ["control-plane-poller"]
    # The poller hands due work to the job manager, not straight to the runner.
    assert isinstance(started[0]["args"][0], JobManager)
    assert started[0]["args"][1] == 60.0
    # It skips the first cycle so a redeploy never triggers a crawl at boot.
    assert started[0]["kwargs"]["skip_first"] is True
