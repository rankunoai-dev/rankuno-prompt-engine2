"""Project-level positioning: consolidate the last N crawls into one position set.

A single crawl (one project run, even with several samples per platform) is a
noisy read: citations and mentions move day to day. The analyst therefore sets,
per project, a *consolidation window* in runs (3 by default). Every crawl is
still stored on its own date; after every N-th full crawl (or on demand) the
positions of every tracked prompt on every platform are recomputed from all
snapshots and answer samples of those N crawls and stored as a separate,
dated set. The run interval (how often the engines crawl) and the window (how
many crawls make one position) are independent settings.

Tables (same SQLite file as the tracker):
  project_runs   — one row per project crawl (job) with the pipeline run ids it produced
  consolidations — one row per consolidation (date, window, runs covered)
  positions      — one row per prompt x platform per consolidation (JSON payload)
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.core.logger import get_logger
from src.core.sqlite import connect
from src.core.stats import wilson_interval
from src.integrations.schemas import Engine
from src.modules.control_plane.planner import effective_engines
from src.modules.control_plane.schemas import (
    ConsolidatedPosition,
    Consolidation,
    PositionsView,
    Project,
    ProjectRunRecord,
    PromptPosition,
    SamplingSummary,
    TrackedPrompt,
)

__all__ = ["PositionStore", "aggregate_position"]

_logger = get_logger("modules.control_plane.positioning")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_runs (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    run_ids     TEXT NOT NULL,
    prompts_run INTEGER NOT NULL,
    batches     INTEGER NOT NULL,
    statuses    TEXT NOT NULL,
    full        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_project_runs_project ON project_runs (project_id, started_at);
CREATE TABLE IF NOT EXISTS consolidations (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    consolidated_at TEXT NOT NULL,
    window_runs     INTEGER NOT NULL,
    project_run_ids TEXT NOT NULL,
    run_ids         TEXT NOT NULL,
    first_run_at    TEXT,
    last_run_at     TEXT,
    prompts         INTEGER NOT NULL,
    trigger         TEXT NOT NULL,
    note            TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_consolidations_project
    ON consolidations (project_id, consolidated_at);
CREATE TABLE IF NOT EXISTS positions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    consolidation_id TEXT NOT NULL REFERENCES consolidations(id),
    prompt_id        TEXT NOT NULL,
    engine           TEXT NOT NULL,
    payload          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_positions_consolidation ON positions (consolidation_id);
-- One prompt's position across every consolidation, for its trend line.
CREATE INDEX IF NOT EXISTS ix_positions_prompt ON positions (prompt_id, engine);
"""

_RUN_COLUMNS = (
    "id, project_id, started_at, finished_at, run_ids, prompts_run, batches, statuses, full, "
    "sampling"
)
_ADDITIONS = {
    ("project_runs", "sampling"): "TEXT",  # SamplingSummary JSON; NULL before ADR 0025
}
_CONS_COLUMNS = (
    "id, project_id, consolidated_at, window_runs, project_run_ids, run_ids, first_run_at, "
    "last_run_at, prompts, trigger, note"
)


def _loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) and value else default
    except json.JSONDecodeError:
        return default


def _maybe_dt(value: Any) -> datetime | None:
    """Parse a nullable ISO timestamp column."""
    return datetime.fromisoformat(str(value)) if value else None


def aggregate_position(
    prompt_id: str,
    engine: Engine,
    snapshots: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    organic: list[dict[str, Any]],
) -> ConsolidatedPosition | None:
    """Fold the window's snapshots (totals), samples (distributions) and organic ranks.

    Returns None when the window holds nothing for this prompt on this engine.
    """
    if not snapshots:
        return None
    total = sum(int(s["samples"]) for s in snapshots)
    failed = sum(int(s["failed_samples"]) for s in snapshots)
    ok = max(total - failed, 0)
    cited = sum(int(s["client_cited_samples"]) for s in snapshots)
    mention_samples = sum(
        round(
            float(s["mention_rate"] or 0.0) * max(int(s["samples"]) - int(s["failed_samples"]), 0)
        )
        for s in snapshots
    )
    ranks = [int(s["client_best_rank"]) for s in snapshots if s["client_best_rank"] is not None]
    weighted = [
        (float(s["client_mean_rank"]), int(s["client_cited_samples"]))
        for s in snapshots
        if s["client_mean_rank"] is not None and int(s["client_cited_samples"]) > 0
    ]
    mean_rank = (
        round(sum(r * w for r, w in weighted) / sum(w for _, w in weighted), 2)
        if weighted
        else None
    )
    competitors: dict[str, int] = {}
    for s in snapshots:
        for domain, rank in _loads(s["competitor_citations"], {}).items():
            competitors[domain] = min(int(rank), competitors.get(domain, int(rank)))

    rank_distribution: dict[str, int] = {}
    domain_hits: dict[str, int] = {}
    if samples:
        for row in samples:
            key = str(row["client_rank"]) if row["client_rank"] is not None else "not cited"
            rank_distribution[key] = rank_distribution.get(key, 0) + 1
            for domain in dict.fromkeys(_loads(row["cited_domains"], [])):
                domain_hits[domain] = domain_hits.get(domain, 0) + 1
        share_base = len(samples)
    else:  # reused snapshots carry no samples of their own: fall back to snapshot fields
        for s in snapshots:
            key = str(s["client_best_rank"]) if s["client_best_rank"] is not None else "not cited"
            rank_distribution[key] = rank_distribution.get(key, 0) + 1
            for domain in dict.fromkeys(_loads(s["cited_domains"], [])):
                domain_hits[domain] = domain_hits.get(domain, 0) + 1
        share_base = len(snapshots)
    domain_share = {
        d: round(n / share_base, 4)
        for d, n in sorted(domain_hits.items(), key=lambda kv: (-kv[1], kv[0]))[:25]
    }

    def _organic(kind: str) -> tuple[int | None, float | None]:
        positions = [
            int(o["client_position"])
            for o in organic
            if o["query_kind"] == kind and o["client_position"] is not None
        ]
        if not positions:
            return None, None
        return min(positions), round(sum(positions) / len(positions), 2)

    prompt_best, prompt_mean = _organic("PROMPT")
    keyword_best, keyword_mean = _organic("KEYWORD")
    captured = sorted(str(s["captured_at"]) for s in snapshots)
    cited_band = wilson_interval(min(cited, ok), ok)
    mention_band = wilson_interval(min(mention_samples, ok), ok)
    return ConsolidatedPosition(
        prompt_id=prompt_id,
        engine=engine,
        runs=len({str(s["run_id"]) for s in snapshots}),
        first_run_at=datetime.fromisoformat(captured[0]),
        last_run_at=datetime.fromisoformat(captured[-1]),
        samples=total,
        failed_samples=failed,
        cited_samples=cited,
        citation_rate=round(cited / ok, 4) if ok else 0.0,
        citation_rate_low=cited_band[0] if cited_band else None,
        citation_rate_high=cited_band[1] if cited_band else None,
        cited=bool(ok) and cited * 2 >= ok,
        mention_samples=mention_samples,
        mention_rate=round(mention_samples / ok, 4) if ok else 0.0,
        mention_rate_low=mention_band[0] if mention_band else None,
        mention_rate_high=mention_band[1] if mention_band else None,
        mentioned=bool(ok) and mention_samples * 2 >= ok,
        best_rank=min(ranks) if ranks else None,
        mean_rank=mean_rank,
        rank_distribution=rank_distribution,
        cited_domain_share=domain_share,
        competitor_citations=competitors,
        organic_prompt_best=prompt_best,
        organic_prompt_mean=prompt_mean,
        organic_keyword_best=keyword_best,
        organic_keyword_mean=keyword_mean,
    )


class PositionStore:
    """Project crawl history and consolidated positions, in the tracker SQLite file."""

    def __init__(self, path: Path) -> None:
        """Open (creating tables if needed) the store at `path`."""
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            for (table, column), definition in _ADDITIONS.items():
                existing = {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}  # noqa: S608
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")  # noqa: S608
                    _logger.info("schema_migrated", extra={"table": table, "column": column})

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self._path, row_factory=sqlite3.Row)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # -- project runs -------------------------------------------------------------

    def record_project_run(self, record: ProjectRunRecord) -> None:
        """Store one project crawl (a job that produced at least one pipeline run)."""
        with self._connect() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO project_runs ({_RUN_COLUMNS}) "  # noqa: S608
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    record.project_id,
                    record.started_at.isoformat(),
                    record.finished_at.isoformat(),
                    json.dumps(record.run_ids),
                    record.prompts_run,
                    record.batches,
                    json.dumps(record.statuses),
                    int(record.full),
                    record.sampling.model_dump_json() if record.sampling else None,
                ),
            )

    def recent_sampling(self, project_id: str, *, limit: int) -> list[SamplingSummary]:
        """Sampling summaries of the newest crawls that carry one, newest first."""
        if limit <= 0:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT sampling FROM project_runs WHERE project_id = ? AND sampling IS NOT NULL "
                "ORDER BY started_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        return [SamplingSummary.model_validate_json(str(r[0])) for r in rows]

    def project_runs(
        self, project_id: str, *, limit: int = 50, full_only: bool = False
    ) -> list[ProjectRunRecord]:
        """Crawls for a project, newest first."""
        where = " AND full = 1" if full_only else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_RUN_COLUMNS} FROM project_runs WHERE project_id = ?{where} "  # noqa: S608
                "ORDER BY started_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        return [self._run_from_row(r) for r in rows]

    def runs_since_last_consolidation(self, project_id: str) -> int:
        """Full crawls not yet covered by a consolidation."""
        latest = self.consolidations(project_id, limit=1)
        cutoff = latest[0].last_run_at if latest and latest[0].last_run_at else None
        with self._connect() as conn:
            if cutoff is None:
                row = conn.execute(
                    "SELECT COUNT(*) FROM project_runs WHERE project_id = ? AND full = 1",
                    (project_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM project_runs WHERE project_id = ? AND full = 1 "
                    "AND started_at > ?",
                    (project_id, cutoff.isoformat()),
                ).fetchone()
        return int(row[0])

    # -- consolidating -------------------------------------------------------------

    def compute(
        self, project: Project, prompts: list[TrackedPrompt], *, window_runs: int
    ) -> tuple[list[ConsolidatedPosition], list[ProjectRunRecord]]:
        """Positions over the last `window_runs` full crawls, without storing anything."""
        crawls = self.project_runs(project.id, limit=window_runs, full_only=True)
        if not crawls:
            return [], []
        run_ids = sorted({rid for c in crawls for rid in c.run_ids})
        positions: list[ConsolidatedPosition] = []
        for prompt in prompts:
            for engine in effective_engines(project, prompt):
                position = aggregate_position(
                    prompt.prompt_id,
                    engine,
                    self._snapshots(prompt.prompt_id, engine, run_ids),
                    self._samples(prompt.prompt_id, engine, run_ids),
                    self._organic(prompt.prompt_id, run_ids),
                )
                if position is not None:
                    positions.append(position)
        return positions, crawls

    def consolidate(
        self,
        project: Project,
        prompts: list[TrackedPrompt],
        *,
        window_runs: int,
        trigger: str,
        note: str = "",
        now: datetime | None = None,
    ) -> Consolidation:
        """Compute and store positions from the last `window_runs` full crawls.

        Raises:
            ValueError: No crawl to consolidate.
        """
        positions, crawls = self.compute(project, prompts, window_runs=window_runs)
        if not crawls:
            msg = "No completed crawl to consolidate yet."
            raise ValueError(msg)
        run_ids = sorted({rid for c in crawls for rid in c.run_ids})
        consolidation = Consolidation(
            id=uuid.uuid4().hex[:16],
            project_id=project.id,
            consolidated_at=now or datetime.now(UTC),
            window_runs=window_runs,
            project_run_ids=[c.id for c in crawls],
            run_ids=run_ids,
            first_run_at=min(c.started_at for c in crawls),
            last_run_at=max(c.started_at for c in crawls),
            prompts=len(prompts),
            trigger=trigger,
            note=note,
        )
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO consolidations ({_CONS_COLUMNS}) "  # noqa: S608
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    consolidation.id,
                    consolidation.project_id,
                    consolidation.consolidated_at.isoformat(),
                    consolidation.window_runs,
                    json.dumps(consolidation.project_run_ids),
                    json.dumps(consolidation.run_ids),
                    consolidation.first_run_at.isoformat() if consolidation.first_run_at else None,
                    consolidation.last_run_at.isoformat() if consolidation.last_run_at else None,
                    consolidation.prompts,
                    consolidation.trigger,
                    consolidation.note,
                ),
            )
            conn.executemany(
                "INSERT INTO positions (consolidation_id, prompt_id, engine, payload) "
                "VALUES (?, ?, ?, ?)",
                [
                    (consolidation.id, p.prompt_id, p.engine.value, p.model_dump_json())
                    for p in positions
                ],
            )
        _logger.info(
            "positions_consolidated",
            extra={
                "project_id": project.id,
                "consolidation_id": consolidation.id,
                "window_runs": window_runs,
                "positions": len(positions),
                "trigger": trigger,
            },
        )
        return consolidation.model_copy(update={"positions_count": len(positions)})

    # -- reading -------------------------------------------------------------------

    def consolidations(self, project_id: str, *, limit: int = 50) -> list[Consolidation]:
        """Consolidations for a project, newest first, with their position counts."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT c.*, (SELECT COUNT(*) FROM positions p WHERE p.consolidation_id = c.id) "  # noqa: S608
                "AS positions_count FROM consolidations c WHERE project_id = ? "
                "ORDER BY consolidated_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        return [self._consolidation_from_row(r) for r in rows]

    def positions_for_prompt(self, project_id: str, prompt_id: str) -> list[PromptPosition]:
        """Every consolidated position for one prompt, oldest first, with its window.

        One join rather than a `positions()` call per consolidation, each of which
        would carry the whole project's prompt × engine set.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT c.id, c.consolidated_at, c.window_runs, c.first_run_at, c.last_run_at, "
                "p.payload FROM positions p JOIN consolidations c ON c.id = p.consolidation_id "
                "WHERE c.project_id = ? AND p.prompt_id = ? ORDER BY c.consolidated_at, p.id",
                (project_id, prompt_id),
            ).fetchall()
        return [
            PromptPosition(
                consolidation_id=str(row["id"]),
                consolidated_at=datetime.fromisoformat(str(row["consolidated_at"])),
                window_runs=int(row["window_runs"]),
                first_run_at=_maybe_dt(row["first_run_at"]),
                last_run_at=_maybe_dt(row["last_run_at"]),
                position=ConsolidatedPosition.model_validate_json(str(row["payload"])),
            )
            for row in rows
        ]

    def positions(self, project_id: str, consolidation_id: str | None = None) -> PositionsView:
        """The latest (or a chosen) consolidated position set, with history and pending count."""
        history = self.consolidations(project_id)
        chosen: Consolidation | None = None
        if consolidation_id is not None:
            chosen = next((c for c in history if c.id == consolidation_id), None)
            if chosen is None:
                raise KeyError(consolidation_id)
        elif history:
            chosen = history[0]
        positions: list[ConsolidatedPosition] = []
        if chosen is not None:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT payload FROM positions WHERE consolidation_id = ? ORDER BY id",
                    (chosen.id,),
                ).fetchall()
            positions = [ConsolidatedPosition.model_validate_json(r["payload"]) for r in rows]
        return PositionsView(
            consolidation=chosen,
            positions=positions,
            history=history,
            runs_since_last=self.runs_since_last_consolidation(project_id),
        )

    # -- internals -----------------------------------------------------------------

    def _snapshots(
        self, prompt_id: str, engine: Engine, run_ids: list[str]
    ) -> list[dict[str, Any]]:
        if not run_ids:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT run_id, captured_at, samples, failed_samples, client_cited_samples, "
                "client_best_rank, client_mean_rank, cited_domains, competitor_citations, "
                "mention_rate FROM snapshots WHERE prompt_id = ? AND engine = ? "
                "AND run_id IN (SELECT value FROM json_each(?)) ORDER BY captured_at",
                (prompt_id, engine.value, json.dumps(run_ids)),
            ).fetchall()
        return [dict(r) for r in rows]

    def _samples(self, prompt_id: str, engine: Engine, run_ids: list[str]) -> list[dict[str, Any]]:
        if not run_ids:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT run_id, client_cited, client_rank, cited_domains, mention_detected "
                "FROM answer_samples WHERE prompt_id = ? AND engine = ? "
                "AND run_id IN (SELECT value FROM json_each(?))",
                (prompt_id, engine.value, json.dumps(run_ids)),
            ).fetchall()
        return [dict(r) for r in rows]

    def _organic(self, prompt_id: str, run_ids: list[str]) -> list[dict[str, Any]]:
        if not run_ids:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT query_kind, client_position FROM organic_snapshots "
                "WHERE prompt_id = ? AND run_id IN (SELECT value FROM json_each(?))",
                (prompt_id, json.dumps(run_ids)),
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> ProjectRunRecord:
        return ProjectRunRecord(
            id=str(row["id"]),
            project_id=str(row["project_id"]),
            started_at=datetime.fromisoformat(str(row["started_at"])),
            finished_at=datetime.fromisoformat(str(row["finished_at"])),
            run_ids=_loads(row["run_ids"], []),
            prompts_run=int(row["prompts_run"]),
            batches=int(row["batches"]),
            statuses=_loads(row["statuses"], []),
            full=bool(row["full"]),
            sampling=(
                SamplingSummary.model_validate_json(str(row["sampling"]))
                if row["sampling"]
                else None
            ),
        )

    @staticmethod
    def _consolidation_from_row(row: sqlite3.Row) -> Consolidation:
        return Consolidation(
            id=str(row["id"]),
            project_id=str(row["project_id"]),
            consolidated_at=datetime.fromisoformat(str(row["consolidated_at"])),
            window_runs=int(row["window_runs"]),
            project_run_ids=_loads(row["project_run_ids"], []),
            run_ids=_loads(row["run_ids"], []),
            first_run_at=_maybe_dt(row["first_run_at"]),
            last_run_at=_maybe_dt(row["last_run_at"]),
            prompts=int(row["prompts"]),
            trigger=str(row["trigger"]),
            note=str(row["note"] or ""),
            positions_count=int(row["positions_count"]),
        )
