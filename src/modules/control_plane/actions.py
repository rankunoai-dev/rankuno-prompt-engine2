"""Analyst state for action cards: open/done, owner, note, and the baseline for outcomes.

Action cards are recomputed from data on every read (`insights.py`) and carry a
stable id derived from what triggered them. This store keeps only what the
analyst adds: status, owner, note, and the metric value at the moment the card
was marked done, so the next consolidation can report improved / unchanged /
regressed without a scheduler.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from src.modules.control_plane.schemas import ActionState

__all__ = ["ActionStateStore"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS action_states (
    project_id TEXT NOT NULL,
    action_id  TEXT NOT NULL,
    status     TEXT NOT NULL,
    owner      TEXT,
    note       TEXT,
    baseline   TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, action_id)
);
"""


class ActionStateStore:
    """Per-project analyst state for action cards, in the tracker SQLite file."""

    def __init__(self, path: Path) -> None:
        """Open (creating the table if needed) the store at `path`."""
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def states(self, project_id: str) -> dict[str, ActionState]:
        """All stored states for a project, keyed by action id."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT action_id, status, owner, note, baseline, updated_at "
                "FROM action_states WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        return {
            str(r["action_id"]): ActionState(
                action_id=str(r["action_id"]),
                status=str(r["status"]),
                owner=r["owner"],
                note=r["note"],
                baseline=json.loads(r["baseline"] or "{}"),
                updated_at=datetime.fromisoformat(str(r["updated_at"])),
            )
            for r in rows
        }

    def set_state(
        self,
        project_id: str,
        action_id: str,
        *,
        status: str | None,
        owner: str | None,
        note: str | None,
        baseline: dict[str, float | str | None] | None,
        now: datetime | None = None,
    ) -> ActionState:
        """Upsert the analyst's state; `baseline` is stored when the card is marked done."""
        current = self.states(project_id).get(action_id)
        merged = ActionState(
            action_id=action_id,
            status=status or (current.status if current else "open"),
            owner=owner if owner is not None else (current.owner if current else None),
            note=note if note is not None else (current.note if current else None),
            baseline=baseline
            if baseline is not None
            else (current.baseline if current and status != "open" else {}),
            updated_at=now or datetime.now(UTC),
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO action_states "
                "(project_id, action_id, status, owner, note, baseline, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    project_id,
                    action_id,
                    merged.status,
                    merged.owner,
                    merged.note,
                    json.dumps(merged.baseline),
                    merged.updated_at.isoformat(),
                ),
            )
        return merged
