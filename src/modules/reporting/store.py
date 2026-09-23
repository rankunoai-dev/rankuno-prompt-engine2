"""Where reports and brand logos live (ADR 0024).

Rows go in the tracker database beside every other project table; the PDFs and
logos go on the volume under `REPORTS_DIR`, because a database is the wrong
place for a megabyte of binary per month.

Two filesystem rules are enforced here and nowhere else:

* Every path is built from an id this module minted, never from caller input.
  `_project_dir` and `logo_path` refuse anything that is not a known-shape id,
  so a crafted `report_id` cannot escape the reports directory.
* Retention deletes the file and marks the row `purged_at`. The row stays: a
  client asking "what did you send me in March" deserves an answer even after
  the artefact is gone.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from src.core.branding import LOGO_ID_PATTERN, Brand
from src.core.logger import get_logger
from src.core.sqlite import connect
from src.modules.reporting.schemas import ReportRecord, ReportRequest, ReportState

__all__ = ["ReportStore"]

_logger = get_logger("modules.reporting.store")

_ID: Final = re.compile(r"^[a-f0-9]{8,32}$")

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS report_runs (
    id               TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL,
    state            TEXT NOT NULL,
    title            TEXT NOT NULL,
    request          TEXT NOT NULL,
    brand            TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    started_at       TEXT,
    finished_at      TEXT,
    file_name        TEXT,
    size_bytes       INTEGER,
    pages            INTEGER,
    narrative_source TEXT,
    narrative_model  TEXT,
    spend_usd        REAL NOT NULL DEFAULT 0,
    window_label     TEXT NOT NULL DEFAULT '',
    emailed_to       INTEGER NOT NULL DEFAULT 0,
    error            TEXT,
    purged_at        TEXT
);
CREATE INDEX IF NOT EXISTS ix_report_runs_project ON report_runs (project_id, created_at DESC);
"""

_COLUMNS: Final = (
    "id, project_id, state, title, request, brand, created_at, started_at, finished_at, "
    "file_name, size_bytes, pages, narrative_source, narrative_model, spend_usd, window_label, "
    "emailed_to, error, purged_at"
)


def _parse(value: str | None) -> datetime | None:
    """Read a stored ISO timestamp back."""
    return datetime.fromisoformat(value) if value else None


def _row(record: tuple[object, ...]) -> ReportRecord:
    """Rebuild a record from its row."""
    return ReportRecord(
        id=str(record[0]),
        project_id=str(record[1]),
        state=ReportState(str(record[2])),
        title=str(record[3]),
        request=ReportRequest.model_validate_json(str(record[4])),
        brand=Brand.model_validate_json(str(record[5])),
        created_at=datetime.fromisoformat(str(record[6])),
        started_at=_parse(record[7] if record[7] is None else str(record[7])),
        finished_at=_parse(record[8] if record[8] is None else str(record[8])),
        file_name=None if record[9] is None else str(record[9]),
        size_bytes=None if record[10] is None else int(record[10]),  # type: ignore[arg-type]
        pages=None if record[11] is None else int(record[11]),  # type: ignore[arg-type]
        narrative_source=None if record[12] is None else str(record[12]),  # type: ignore[arg-type]
        narrative_model=None if record[13] is None else str(record[13]),
        spend_usd=float(record[14]),  # type: ignore[arg-type]
        window_label=str(record[15]),
        emailed_to=int(record[16]),  # type: ignore[arg-type]
        error=None if record[17] is None else str(record[17]),
        purged_at=_parse(record[18] if record[18] is None else str(record[18])),
    )


class ReportStore:
    """Rows in SQLite, artefacts on the volume."""

    def __init__(self, db_path: Path, reports_dir: Path) -> None:
        """Create the schema and make sure the directories exist."""
        self._path = db_path
        self._root = reports_dir
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
        (self._root / "branding").mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open the tracker database with the platform pragmas."""
        with connect(self._path) as conn:
            yield conn

    # -- paths ------------------------------------------------------------

    def _project_dir(self, project_id: str) -> Path:
        """The folder holding one project's PDFs, created on demand."""
        if not _ID.match(project_id):
            msg = f"{project_id!r} is not a project id."
            raise ValueError(msg)
        path = self._root / "projects" / project_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def file_path(self, record: ReportRecord) -> Path | None:
        """Where `record`'s PDF is, or None when there is nothing to serve."""
        if record.file_name is None:
            return None
        if not _ID.match(record.file_name.removesuffix(".pdf")):
            msg = "stored file name is not a report id."
            raise ValueError(msg)
        return self._project_dir(record.project_id) / record.file_name

    def logo_path(self, logo_id: str) -> Path:
        """Where a stored logo is; refuses any name this app did not mint."""
        if not LOGO_ID_PATTERN.match(logo_id):
            msg = "logo id must be a stored logo name."
            raise ValueError(msg)
        return self._root / "branding" / logo_id

    def save_logo(self, data: bytes, suffix: str) -> str:
        """Write a validated image and return its id."""
        logo_id = f"{uuid.uuid4().hex[:16]}.{suffix}"
        self.logo_path(logo_id).write_bytes(data)
        return logo_id

    # -- rows -------------------------------------------------------------

    def create(
        self, project_id: str, request: ReportRequest, brand: Brand, title: str
    ) -> ReportRecord:
        """Queue a report and return its row."""
        record = ReportRecord(
            id=uuid.uuid4().hex[:16],
            project_id=project_id,
            state=ReportState.QUEUED,
            request=request,
            brand=brand,
            title=title,
            created_at=datetime.now(UTC),
        )
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO report_runs ({_COLUMNS}) VALUES "  # noqa: S608 - constant columns, bound values
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.id,
                    record.project_id,
                    record.state.value,
                    record.title,
                    record.request.model_dump_json(),
                    record.brand.model_dump_json(),
                    record.created_at.isoformat(),
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    0.0,
                    "",
                    0,
                    None,
                    None,
                ),
            )
        return record

    def save(self, record: ReportRecord) -> ReportRecord:
        """Write every mutable column of `record` back."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE report_runs SET state=?, started_at=?, finished_at=?, file_name=?, "
                "size_bytes=?, pages=?, narrative_source=?, narrative_model=?, spend_usd=?, "
                "window_label=?, emailed_to=?, error=?, purged_at=? WHERE id=?",
                (
                    record.state.value,
                    record.started_at.isoformat() if record.started_at else None,
                    record.finished_at.isoformat() if record.finished_at else None,
                    record.file_name,
                    record.size_bytes,
                    record.pages,
                    record.narrative_source.value if record.narrative_source else None,
                    record.narrative_model,
                    record.spend_usd,
                    record.window_label,
                    record.emailed_to,
                    record.error,
                    record.purged_at.isoformat() if record.purged_at else None,
                    record.id,
                ),
            )
        return record

    def write_pdf(self, record: ReportRecord, data: bytes) -> str:
        """Store the artefact and return the file name to record."""
        name = f"{record.id}.pdf"
        (self._project_dir(record.project_id) / name).write_bytes(data)
        return name

    def get(self, report_id: str) -> ReportRecord | None:
        """One report, or None."""
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM report_runs WHERE id=?",  # noqa: S608 - constant columns
                (report_id,),
            ).fetchone()
        return _row(row) if row else None

    def list_for(self, project_id: str, limit: int = 50) -> list[ReportRecord]:
        """A project's reports, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM report_runs WHERE project_id=? "  # noqa: S608 - constant columns
                "ORDER BY created_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        return [_row(row) for row in rows]

    def delete(self, report_id: str) -> bool:
        """Remove a report and its file; True when something was deleted."""
        record = self.get(report_id)
        if record is None:
            return False
        path = self.file_path(record)
        if path is not None and path.exists():
            path.unlink()
        with self._connect() as conn:
            conn.execute("DELETE FROM report_runs WHERE id=?", (report_id,))
        return True

    def delete_project(self, project_id: str) -> None:
        """Drop every row and file for a project, when the project goes."""
        for record in self.list_for(project_id, limit=10_000):
            path = self.file_path(record)
            if path is not None and path.exists():
                path.unlink()
        with self._connect() as conn:
            conn.execute("DELETE FROM report_runs WHERE project_id=?", (project_id,))

    def purge(self, retention_days: int) -> int:
        """Delete artefacts older than the window; keep the rows. Returns the count."""
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        purged = 0
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM report_runs WHERE purged_at IS NULL "  # noqa: S608 - constant columns
                "AND file_name IS NOT NULL AND created_at < ?",
                (cutoff.isoformat(),),
            ).fetchall()
        for row in rows:
            record = _row(row)
            path = self.file_path(record)
            if path is not None and path.exists():
                path.unlink()
            record.purged_at = datetime.now(UTC)
            self.save(record)
            purged += 1
        if purged:
            _logger.info("reports_purged", extra={"count": purged, "days": retention_days})
        return purged
