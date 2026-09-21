"""Start the control-plane API and UI: `python -m src.modules.control_plane`.

Binds to `HOST:PORT` from settings (127.0.0.1:8787 by default; a container sets
`HOST=0.0.0.0` and the platform injects `PORT`). Runs are queued to one
background worker (`JobManager`), so the UI gets progress instead of a blocked
request. With `--poll-minutes N` a poller thread queues due work for every
enabled project every N minutes; those runs are approved by budget
(`UNATTENDED_SPEND_CAP_USD`). `--approve-spend` makes every run started from
this process (UI clicks included) count as operator-approved — which is why
every route but `/api/health` sits behind HTTP Basic auth once
`CONTROL_PLANE_USER`/`CONTROL_PLANE_PASSWORD` are set. Two ceilings apply to
spend: `MAX_SESSION_SPEND_USD` per process and `DAILY_SPEND_CAP_USD` read back
from the usage ledger, so a restart does not re-arm the budget.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from datetime import time as dtime
from typing import Protocol

import uvicorn

from src.core.config import Settings, get_settings
from src.core.guardrails import (
    BudgetedApprovalProvider,
    CallbackApprovalProvider,
    GuardrailEngine,
)
from src.core.logger import get_logger
from src.core.rate_limiter import CostLedger
from src.core.schemas import ToolMetadata
from src.integrations.usage import get_usage_ledger
from src.modules.control_plane.app import create_app
from src.modules.control_plane.jobs import JobManager
from src.modules.control_plane.runner import ProjectRunner
from src.modules.control_plane.store import ProjectStore
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB

__all__ = ["build_parser", "build_runner", "main", "poll_forever"]

_logger = get_logger("modules.control_plane.server")


class DueRunner(Protocol):
    """Anything the poller can hand a cycle to: the runner (sync) or the job manager."""

    def run_due_all(self) -> list[object]:
        """Run or queue due work for every enabled project."""
        ...


def build_parser() -> argparse.ArgumentParser:
    """CLI schema. Host and port fall back to `HOST` / `PORT` settings when omitted."""
    parser = argparse.ArgumentParser(prog="control_plane", description="Prompt Engine UI + API.")
    parser.add_argument("--host", default=None, help="Bind address (default: HOST setting).")
    parser.add_argument("--port", type=int, default=None, help="Port (default: PORT setting).")
    parser.add_argument(
        "--poll-minutes",
        type=float,
        default=0.0,
        help="Queue due work for all projects every N minutes (0 = manual runs only).",
    )
    parser.add_argument("--approve-spend", action="store_true", help="Operator approves spend.")
    return parser


def _spent_today(settings: Settings) -> Callable[[], float]:
    """Actual spend recorded since 00:00 UTC, excluding seeded demo rows.

    Handed to `CostLedger` so the daily cap is measured against the durable
    ledger rather than an in-memory counter that a restart resets.
    """
    ledger = get_usage_ledger(settings)

    def spent() -> float:
        midnight = datetime.combine(datetime.now(UTC).date(), dtime.min, tzinfo=UTC)
        return ledger.spent_since(midnight)

    return spent


def build_runner(settings: Settings, *, approve: bool) -> ProjectRunner:
    """Wire store, time-series DB, guardrails and ledger into a runner."""
    ledger = CostLedger(
        settings.max_session_spend_usd,
        daily_ceiling_usd=settings.daily_spend_cap_usd,
        spent_today=_spent_today(settings),
    )
    if approve:

        def approver(metadata: ToolMetadata, context: str) -> bool:
            _logger.info("operator_ui_approval", extra={"tool": metadata.name, "context": context})
            return True

        guardrails = GuardrailEngine(CallbackApprovalProvider(approver), settings=settings)
    elif settings.unattended_spend_cap_usd > 0:
        provider = BudgetedApprovalProvider(
            ledger, per_action_cap_usd=settings.unattended_spend_cap_usd
        )
        guardrails = GuardrailEngine(provider, settings=settings)
    else:
        guardrails = GuardrailEngine(settings=settings)
    db = TimeSeriesDB(settings.tracker_db_path)
    store = ProjectStore(settings.tracker_db_path)
    return ProjectRunner(store, db, settings=settings, guardrails=guardrails, ledger=ledger)


def poll_forever(
    runner: DueRunner,
    poll_s: float,
    *,
    stop: threading.Event,
    sleep: Callable[[float], None] = time.sleep,
    max_cycles: int | None = None,
) -> int:
    """Hand due work for all projects to `runner` every `poll_s` seconds until `stop` is set."""
    cycles = 0
    while not stop.is_set() and (max_cycles is None or cycles < max_cycles):
        try:
            outcomes = runner.run_due_all()
            _logger.info("poll_cycle", extra={"projects": len(outcomes)})
        except Exception:  # noqa: BLE001 - the poller must survive one bad cycle
            _logger.exception("poll_cycle_failed")
        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            break
        sleep(poll_s)
    return cycles


def main(argv: list[str] | None = None) -> int:
    """Start the server."""
    args = build_parser().parse_args(argv)
    settings = get_settings()
    host = args.host or settings.host
    port = args.port or settings.port
    if not settings.basic_auth_configured:
        # Production refuses to boot without credentials (config.model_post_init);
        # elsewhere the loopback bind is the only guard, so say so every start.
        _logger.warning("control_plane_unauthenticated", extra={"host": host, "port": port})
    runner = build_runner(settings, approve=args.approve_spend)
    store = ProjectStore(settings.tracker_db_path)
    jobs = JobManager(runner, store)
    app = create_app(
        store, TimeSeriesDB(settings.tracker_db_path), runner, jobs, settings=lambda: settings
    )
    stop = threading.Event()
    if args.poll_minutes > 0:
        threading.Thread(
            target=poll_forever,
            args=(jobs, args.poll_minutes * 60.0),
            kwargs={"stop": stop},
            daemon=True,
            name="control-plane-poller",
        ).start()
    sys.stdout.write(f"Control plane: http://{host}:{port}/\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")
    stop.set()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
