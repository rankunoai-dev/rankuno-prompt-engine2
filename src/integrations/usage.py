"""Per-call usage ledger: every outbound vendor request, durably, with its cost.

`BaseAPIClient.call()` records one `ApiCall` per request (ok, error or refused
by the breaker); the connector then enriches it with what the vendor reported
(tokens, search invocations, units, vendor-stated cost) and a list-price model.
Rows live in the tracker SQLite file (`api_calls`), so the control plane, the
CLI and the costing report read one source of truth.

Context (`usage_context`) tags calls with where they came from (`source`), the
run, the prompt and the engine, so volume can be broken down per platform per
prompt. Context is a `contextvars` value; the pipeline copies it into worker
threads explicitly.
"""

from __future__ import annotations

import contextvars
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field

from src.core.config import Settings, get_settings
from src.core.logger import get_logger
from src.core.schemas import StrictModel
from src.core.sqlite import connect

__all__ = [
    "ApiCall",
    "UsageLedger",
    "current_usage_context",
    "get_usage_ledger",
    "usage_context",
]

_logger = get_logger("integrations.usage")

_CONTEXT: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "usage_context", default=None
)
_CONTEXT_KEYS = ("source", "run_id", "prompt_id", "engine")


class ApiCall(StrictModel):
    """One outbound request to a vendor, as recorded by the ledger."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    vendor: str
    operation: str
    source: str = "unknown"
    run_id: str | None = None
    prompt_id: str | None = None
    engine: str | None = None
    model: str | None = None
    status: str = Field(default="ok", description="ok | error | refused")
    error: str | None = None
    latency_ms: float = Field(default=0.0, ge=0.0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_tokens: int | None = Field(default=None, ge=0)
    search_calls: int = Field(default=0, ge=0, description="Web searches / tool invocations.")
    units: int = Field(default=0, ge=0, description="Vendor units (Semrush).")
    estimated_cost_usd: float = Field(default=0.0, ge=0.0, description="Configured COST_*.")
    vendor_cost_usd: float | None = Field(default=None, ge=0.0, description="Vendor-reported.")
    modelled_cost_usd: float | None = Field(default=None, ge=0.0, description="List-price model.")
    note: str | None = None

    @property
    def actual_cost_usd(self) -> float:
        """Best available cost: vendor-reported, else modelled, else the estimate."""
        if self.vendor_cost_usd is not None:
            return self.vendor_cost_usd
        if self.modelled_cost_usd is not None:
            return self.modelled_cost_usd
        return self.estimated_cost_usd


_COLUMNS = tuple(ApiCall.model_fields)
_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_calls (
    id                 TEXT PRIMARY KEY,
    ts                 TEXT NOT NULL,
    vendor             TEXT NOT NULL,
    operation          TEXT NOT NULL,
    source             TEXT NOT NULL,
    run_id             TEXT,
    prompt_id          TEXT,
    engine             TEXT,
    model              TEXT,
    status             TEXT NOT NULL,
    error              TEXT,
    latency_ms         REAL NOT NULL DEFAULT 0,
    input_tokens       INTEGER,
    output_tokens      INTEGER,
    cached_tokens      INTEGER,
    search_calls       INTEGER NOT NULL DEFAULT 0,
    units              INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0,
    vendor_cost_usd    REAL,
    modelled_cost_usd  REAL,
    note               TEXT
);
CREATE INDEX IF NOT EXISTS ix_api_calls_ts ON api_calls (ts);
CREATE INDEX IF NOT EXISTS ix_api_calls_run ON api_calls (run_id);
CREATE INDEX IF NOT EXISTS ix_api_calls_vendor ON api_calls (vendor);
CREATE INDEX IF NOT EXISTS ix_api_calls_prompt ON api_calls (prompt_id, ts);
"""

# Actual cost with the same precedence as `ApiCall.actual_cost_usd`.
_SPENT_SINCE_SQL = (
    "SELECT COALESCE(SUM(COALESCE(vendor_cost_usd, modelled_cost_usd, estimated_cost_usd)), 0) "
    "FROM api_calls WHERE status = 'ok' AND ts >= ?"
)


@contextmanager
def usage_context(**fields: str | None) -> Iterator[None]:
    """Tag calls made inside the block with `source`, `run_id`, `prompt_id`, `engine`.

    Passing `None` for a key *clears* an inherited value, so a keyword-rank
    lookup nested inside a prompt's context is recorded as unattributed rather
    than charged to that prompt.
    """
    unknown = set(fields) - set(_CONTEXT_KEYS)
    if unknown:
        msg = f"Unknown usage context keys: {sorted(unknown)}"
        raise ValueError(msg)
    layered: dict[str, str | None] = {**(_CONTEXT.get() or {}), **fields}
    merged = {k: v for k, v in layered.items() if v is not None}
    token = _CONTEXT.set(merged)
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def current_usage_context() -> dict[str, str]:
    """The tags in effect for the calling thread/task."""
    return dict(_CONTEXT.get() or {})


class UsageLedger:
    """Append-only store of `ApiCall` rows in the tracker SQLite file."""

    def __init__(self, path: Path) -> None:
        """Open (creating if needed) the ledger at `path`."""
        self._path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @property
    def path(self) -> Path:
        """Location of the SQLite file."""
        return self._path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self._path)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def record(self, call: ApiCall) -> None:
        """Insert one call. Never raises into the caller: a ledger fault is logged."""
        row = call.model_dump(mode="json")
        placeholders = ", ".join("?" for _ in _COLUMNS)
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    f"INSERT OR REPLACE INTO api_calls ({', '.join(_COLUMNS)}) "  # noqa: S608
                    f"VALUES ({placeholders})",
                    tuple(row[c] for c in _COLUMNS),
                )
        except sqlite3.Error as exc:
            _logger.error("usage_record_failed", extra={"error": str(exc), "call_id": call.id})

    def update(self, call_id: str, **fields: Any) -> None:
        """Enrich a recorded call (tokens, cost, model…). Unknown fields are rejected."""
        unknown = set(fields) - set(_COLUMNS) - {"id"}
        if unknown:
            msg = f"Unknown ApiCall fields: {sorted(unknown)}"
            raise ValueError(msg)
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    f"UPDATE api_calls SET {assignments} WHERE id = ?",  # noqa: S608
                    (*fields.values(), call_id),
                )
        except sqlite3.Error as exc:
            _logger.error("usage_update_failed", extra={"error": str(exc), "call_id": call_id})

    def calls(
        self,
        *,
        since: datetime | None = None,
        run_ids: list[str] | None = None,
        source: str | None = None,
        vendor: str | None = None,
        prompt_id: str | None = None,
        limit: int | None = None,
    ) -> list[ApiCall]:
        """Rows matching the filters, oldest first.

        `prompt_id` matches only calls made inside a prompt's `usage_context` —
        the engine samples. Harvest, keyword-rank and redirect calls carry no
        prompt and are never returned under it.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if since is not None:
            clauses.append("ts >= ?")
            params.append(since.isoformat())
        if run_ids is not None:
            if not run_ids:
                return []
            clauses.append(f"run_id IN ({', '.join('?' for _ in run_ids)})")
            params.extend(run_ids)
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if vendor is not None:
            clauses.append("vendor = ?")
            params.append(vendor)
        if prompt_id is not None:
            clauses.append("prompt_id = ?")
            params.append(prompt_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        tail = f" LIMIT {int(limit)}" if limit else ""
        query = f"SELECT {', '.join(_COLUMNS)} FROM api_calls{where} ORDER BY ts{tail}"  # noqa: S608
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [ApiCall.model_validate(dict(zip(_COLUMNS, r, strict=True))) for r in rows]

    def count(self) -> int:
        """Number of recorded calls."""
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM api_calls").fetchone()[0])

    def spent_since(
        self, since: datetime, *, exclude_sources: tuple[str, ...] = ("demo",)
    ) -> float:
        """Actual spend of successful calls since `since`, vendor-reported first.

        Same precedence as `ApiCall.actual_cost_usd` (vendor → modelled →
        estimate), computed in SQL so the daily-cap check costs one indexed sum
        rather than a row fetch. Seeded demonstration rows are excluded by default.
        """
        placeholders = ", ".join("?" for _ in exclude_sources)
        exclusion = f" AND source NOT IN ({placeholders})" if exclude_sources else ""
        query = _SPENT_SINCE_SQL + exclusion  # noqa: S608 - `exclusion` is only `?` placeholders
        with self._connect() as conn:
            row = conn.execute(query, (since.isoformat(), *exclude_sources)).fetchone()
        return round(float(row[0] or 0.0), 6)


_LEDGERS: dict[str, UsageLedger] = {}
_LEDGERS_LOCK = threading.Lock()


def get_usage_ledger(settings: Settings | None = None) -> UsageLedger:
    """Process-wide ledger for the configured tracker database (one per path)."""
    path = Path((settings or get_settings()).tracker_db_path)
    key = str(path.resolve())
    with _LEDGERS_LOCK:
        ledger = _LEDGERS.get(key)
        if ledger is None:
            ledger = UsageLedger(path)
            _LEDGERS[key] = ledger
        return ledger
