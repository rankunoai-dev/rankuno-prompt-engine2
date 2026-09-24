"""Generates reports off the request thread (ADR 0024).

A report takes a second to compute, a few to draw and up to thirty waiting for
the narrative model, so the HTTP request returns a queued row and this worker
does the work. It is a separate queue from `control_plane.jobs`, which exists
to serialise *crawls*: a report spends no vendor quota worth serialising and
must not sit behind a twenty-minute crawl to be drawn.

Failure policy, in order:

* The narrative is optional (`narrative.compose` never raises).
* Email is optional: a delivery failure is recorded on the row and the report
  still exists to download.
* Anything else marks the row `failed` with the reason, and the worker keeps
  serving the next report.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from src.core.config import Settings, get_settings
from src.core.errors import IntegrationError
from src.core.logger import get_logger
from src.integrations.anthropic_judge import AnthropicJudgeClient
from src.integrations.email_send import Attachment, EmailClient
from src.integrations.usage import usage_context
from src.modules.control_plane.schemas import (
    ConsolidatedPosition,
    InsightsView,
    PositionsView,
    Project,
)
from src.modules.reporting.facts import build_fact_sheet, window_label
from src.modules.reporting.narrative import compose
from src.modules.reporting.pdf import cover_title, render
from src.modules.reporting.schemas import ReportRecord, ReportState
from src.modules.reporting.store import ReportStore

__all__ = ["ReportInputs", "ReportWorker"]

_logger = get_logger("modules.reporting.worker")

_QUEUE_LIMIT: Final = 20


@dataclass(frozen=True)
class ReportInputs:
    """Everything one report reads, gathered by the caller.

    Passed in rather than fetched here so this package never reaches into the
    control plane's stores, and so a test can build a report from fixtures.
    """

    project: Project
    insights: InsightsView
    positions: PositionsView
    previous_positions: list[ConsolidatedPosition]
    spend_usd: float | None = None


class QueueFull(RuntimeError):
    """Raised when too many reports are already waiting."""

    def __init__(self, waiting: int) -> None:
        """Say how full the queue was."""
        super().__init__(f"{waiting} report(s) already queued; retry when they finish.")
        self.waiting = waiting


class ReportWorker:
    """One background thread turning queued rows into PDFs."""

    def __init__(
        self,
        store: ReportStore,
        loader: Callable[[str, str | None], ReportInputs],
        *,
        settings: Settings | None = None,
        judge: AnthropicJudgeClient | None = None,
        mailer: EmailClient | None = None,
        autostart: bool = True,
    ) -> None:
        """`loader` gathers a project's window.

        The thread starts on the first `submit()`, not here: most processes
        that build a control plane (the CLI, the tests, a deploy that only
        serves reads) never ask for a report, and should not carry an idle
        thread for the life of the process.

        `autostart=False` means this worker never runs a thread at all: it
        queues, and the caller drives it with `generate()`.
        """
        self._store = store
        self._loader = loader
        self._settings = settings or get_settings()
        self._judge = judge
        self._mailer = mailer
        self._queue: queue.Queue[str] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._background = autostart

    def _start(self) -> None:
        """Start the worker thread once, unless this worker is driven by hand."""
        if not self._background:
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            thread = threading.Thread(target=self._serve, name="report-worker", daemon=True)
            self._thread = thread
            thread.start()

    def submit(self, record: ReportRecord) -> ReportRecord:
        """Queue a created row for generation."""
        waiting = self._queue.qsize()
        if waiting >= _QUEUE_LIMIT:
            raise QueueFull(waiting)
        self._queue.put(record.id)
        self._start()
        return record

    def _serve(self) -> None:
        """Drain the queue forever."""
        while True:
            report_id = self._queue.get()
            try:
                record = self._store.get(report_id)
                if record is not None and record.state is ReportState.QUEUED:
                    self.generate(record)
            except Exception:  # noqa: BLE001 - one bad report must not kill the worker
                _logger.exception("report_worker_error", extra={"report_id": report_id})
            finally:
                self._queue.task_done()

    def generate(self, record: ReportRecord) -> ReportRecord:
        """Build one report end to end and return the finished row."""
        record.state = ReportState.RUNNING
        record.started_at = datetime.now(UTC)
        self._store.save(record)
        try:
            # Ledger rows for the narrative are tagged as report spend rather than
            # attributed to a prompt or a crawl, so the Costs page can separate them.
            with usage_context(source="report", run_id=record.id, prompt_id=None, engine=None):
                record = self._build(record)
        except Exception as error:  # noqa: BLE001 - the row carries the reason
            record.state = ReportState.FAILED
            record.error = f"{type(error).__name__}: {error}"[:500]
            record.finished_at = datetime.now(UTC)
            _logger.warning("report_failed", extra={"report_id": record.id, "error": record.error})
            return self._store.save(record)
        record.state = ReportState.DONE
        record.finished_at = datetime.now(UTC)
        _logger.info(
            "report_ready",
            extra={
                "report_id": record.id,
                "pages": record.pages,
                "bytes": record.size_bytes,
                "narrative": record.narrative_source.value if record.narrative_source else None,
                "spend_usd": record.spend_usd,
            },
        )
        return self._store.save(record)

    def _build(self, record: ReportRecord) -> ReportRecord:
        """Facts, words, pages, file, mail — in that order."""
        inputs = self._loader(record.project_id, record.request.consolidation_id)
        facts = build_fact_sheet(
            inputs.project,
            inputs.insights,
            inputs.positions,
            brand=record.brand,
            previous_positions=inputs.previous_positions,
            spend_usd=inputs.spend_usd,
            include_actions=record.request.include_actions,
            include_sentiment=record.request.include_sentiment,
            include_pages=record.request.include_pages,
        )
        narrative = compose(
            facts,
            client=self._judge,
            settings=self._settings,
            enabled=record.request.narrative,
        )
        logo = None
        if record.brand.logo_id:
            candidate = self._store.logo_path(record.brand.logo_id)
            logo = candidate if candidate.exists() else None
        data, pages = render(
            facts,
            narrative,
            record.brand,
            title=cover_title(facts, record.title),
            logo=logo,
        )
        record.file_name = self._store.write_pdf(record, data)
        record.size_bytes = len(data)
        record.pages = pages
        record.narrative_source = narrative.source
        record.narrative_model = narrative.model
        record.spend_usd = narrative.spend_usd
        record.window_label = window_label(facts.window)
        self._store.save(record)
        if record.request.email_to:
            record.emailed_to = self._deliver(record, facts.client_brand, data)
        return record

    def _deliver(self, record: ReportRecord, client_brand: str, data: bytes) -> int:
        """Email the PDF; a failure is recorded, never raised."""
        mailer = self._mailer or EmailClient(self._settings)
        if not mailer.configured:
            record.error = "Report generated; email was not sent because SMTP is not configured."
            return 0
        body = (
            f"{record.title}\n\n"
            f"Window: {record.window_label}\n"
            f"Prepared for {client_brand}"
            + (f" by {record.brand.agency_name}" if record.brand.agency_name else "")
            + ".\n\nThe report is attached as a PDF."
        )
        try:
            return mailer.send(
                to=record.request.email_to,
                subject=f"{record.title} — {client_brand}",
                body=body,
                attachments=[Attachment(f"{record.title[:60]}.pdf", data)],
                display_name=record.brand.agency_name,
            )
        except (IntegrationError, ValueError) as error:
            record.error = f"Report generated; email failed: {error}"[:500]
            _logger.warning("report_email_failed", extra={"report_id": record.id})
            return 0
