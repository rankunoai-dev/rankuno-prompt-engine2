"""Persistence for crawler-log imports, in the tracker database.

Rows are keyed per import, so deleting an upload is exact and re-uploading the
same file is refused rather than double counted. Two uploads that cover the
same day are resolved at read time: for each day, the import with the most
parsed lines wins, then the newest. Retention keys on the log day, not the
upload date, so an old archive does not linger.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from src.core.logger import get_logger
from src.core.sqlite import connect
from src.modules.crawler_logs.ingest import AggregateRow, IngestResult
from src.modules.crawler_logs.schemas import CrawlerImportRecord

__all__ = ["CrawlerLogStore", "DuplicateImport", "HitRow"]

_logger = get_logger("modules.crawler_logs.store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS crawler_imports (
    id                 TEXT PRIMARY KEY,
    project_id         TEXT NOT NULL,
    imported_at        TEXT NOT NULL,
    format             TEXT NOT NULL,
    content_sha256     TEXT NOT NULL,
    lines              INTEGER NOT NULL,
    parsed             INTEGER NOT NULL,
    matched            INTEGER NOT NULL,
    span_from          TEXT,
    span_to            TEXT,
    verification_basis TEXT NOT NULL,
    sampled            INTEGER NOT NULL DEFAULT 0,
    note               TEXT NOT NULL DEFAULT '',
    purged_at          TEXT,
    UNIQUE (project_id, content_sha256)
);
CREATE TABLE IF NOT EXISTS crawler_import_days (
    import_id  TEXT NOT NULL REFERENCES crawler_imports(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL,
    day        TEXT NOT NULL,
    lines      INTEGER NOT NULL,
    PRIMARY KEY (import_id, day)
);
CREATE INDEX IF NOT EXISTS ix_crawler_days_project ON crawler_import_days (project_id, day);
CREATE TABLE IF NOT EXISTS crawler_hits (
    import_id     TEXT NOT NULL REFERENCES crawler_imports(id) ON DELETE CASCADE,
    project_id    TEXT NOT NULL,
    day           TEXT NOT NULL,
    bot           TEXT NOT NULL,
    url_key       TEXT NOT NULL,
    hits          INTEGER NOT NULL,
    s2xx          INTEGER NOT NULL DEFAULT 0,
    s304          INTEGER NOT NULL DEFAULT 0,
    s3xx          INTEGER NOT NULL DEFAULT 0,
    s4xx          INTEGER NOT NULL DEFAULT 0,
    s5xx          INTEGER NOT NULL DEFAULT 0,
    blocked       INTEGER NOT NULL DEFAULT 0,
    verified_hits INTEGER,
    first_seen    TEXT,
    last_seen     TEXT,
    PRIMARY KEY (import_id, day, bot, url_key)
);
CREATE INDEX IF NOT EXISTS ix_crawler_hits_project_day ON crawler_hits (project_id, day);
CREATE INDEX IF NOT EXISTS ix_crawler_hits_project_url ON crawler_hits (project_id, url_key);
CREATE TABLE IF NOT EXISTS crawler_stealth (
    import_id  TEXT NOT NULL REFERENCES crawler_imports(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL,
    day        TEXT NOT NULL,
    vendor     TEXT NOT NULL,
    hits       INTEGER NOT NULL,
    PRIMARY KEY (import_id, day, vendor)
);
"""


class DuplicateImport(RuntimeError):
    """The same log content was already imported into this project."""

    def __init__(self, import_id: str) -> None:
        """Name the earlier import."""
        super().__init__(f"This log was already imported as {import_id}.")
        self.import_id = import_id


HitRow = AggregateRow


class CrawlerLogStore:
    """Imports, per-day hit aggregates and stealth counters for every project."""

    def __init__(self, path: Path) -> None:
        """Open (creating if needed) the tables in the tracker database."""
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

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

    # -- writing -------------------------------------------------------------------

    def record_import(
        self, project_id: str, result: IngestResult, *, note: str = "", now: datetime | None = None
    ) -> CrawlerImportRecord:
        """Persist one import's aggregates. Raises `DuplicateImport` on the same content."""
        stats = result.stats
        imported_at = now or datetime.now(UTC)
        import_id = uuid.uuid4().hex[:16]
        with self._connect() as conn:
            dup = conn.execute(
                "SELECT id FROM crawler_imports WHERE project_id = ? AND content_sha256 = ?",
                (project_id, stats.content_sha256),
            ).fetchone()
            if dup is not None:
                raise DuplicateImport(str(dup["id"]))
            conn.execute(
                "INSERT INTO crawler_imports (id, project_id, imported_at, format, "
                "content_sha256, lines, parsed, matched, span_from, span_to, "
                "verification_basis, sampled, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    import_id,
                    project_id,
                    imported_at.isoformat(),
                    stats.format,
                    stats.content_sha256,
                    stats.lines,
                    stats.parsed,
                    result.matched,
                    stats.span_from.date().isoformat() if stats.span_from else None,
                    stats.span_to.date().isoformat() if stats.span_to else None,
                    result.verification_basis,
                    int(stats.sampled),
                    note[:500],
                ),
            )
            conn.executemany(
                "INSERT INTO crawler_import_days (import_id, project_id, day, lines) "
                "VALUES (?,?,?,?)",
                [(import_id, project_id, day, n) for day, n in sorted(stats.day_lines.items())],
            )
            rows = list(result.rows.values())
            for start in range(0, len(rows), 1000):
                conn.executemany(
                    "INSERT INTO crawler_hits (import_id, project_id, day, bot, url_key, hits, "
                    "s2xx, s304, s3xx, s4xx, s5xx, blocked, verified_hits, first_seen, last_seen) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            import_id,
                            project_id,
                            r.day,
                            r.bot,
                            r.url_key,
                            r.hits,
                            r.s2xx,
                            r.s304,
                            r.s3xx,
                            r.s4xx,
                            r.s5xx,
                            r.blocked,
                            r.verified,
                            r.first_seen.isoformat() if r.first_seen else None,
                            r.last_seen.isoformat() if r.last_seen else None,
                        )
                        for r in rows[start : start + 1000]
                    ],
                )
            conn.executemany(
                "INSERT INTO crawler_stealth (import_id, project_id, day, vendor, hits) "
                "VALUES (?,?,?,?,?)",
                [(import_id, project_id, d, v, n) for (d, v), n in result.stealth.items()],
            )
        _logger.info(
            "crawler_log_imported",
            extra={"project_id": project_id, "import_id": import_id, "hits": result.hits},
        )
        return self.get_import(project_id, import_id)

    def delete_import(self, project_id: str, import_id: str) -> None:
        """Remove one upload and everything derived from it."""
        with self._connect() as conn:
            gone = conn.execute(
                "DELETE FROM crawler_imports WHERE project_id = ? AND id = ?",
                (project_id, import_id),
            ).rowcount
        if not gone:
            raise KeyError(import_id)

    def delete_project_data(self, project_id: str) -> None:
        """Remove every import of a project (no foreign key reaches `projects`)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM crawler_imports WHERE project_id = ?", (project_id,))

    def purge(self, retention_days: int, *, today: date | None = None) -> int:
        """Drop hits older than the retention window; keep import rows as provenance."""
        cutoff = ((today or datetime.now(UTC).date()) - timedelta(days=retention_days)).isoformat()
        stamp = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            removed = conn.execute("DELETE FROM crawler_hits WHERE day < ?", (cutoff,)).rowcount
            conn.execute("DELETE FROM crawler_stealth WHERE day < ?", (cutoff,))
            conn.execute("DELETE FROM crawler_import_days WHERE day < ?", (cutoff,))
            conn.execute(
                "UPDATE crawler_imports SET purged_at = ? WHERE purged_at IS NULL AND id NOT IN "
                "(SELECT DISTINCT import_id FROM crawler_import_days)",
                (stamp,),
            )
        if removed:
            _logger.info("crawler_hits_purged", extra={"rows": removed, "before": cutoff})
        return int(removed)

    # -- reading -------------------------------------------------------------------

    def get_import(self, project_id: str, import_id: str) -> CrawlerImportRecord:
        """One import, or `KeyError`."""
        found = [r for r in self.imports(project_id) if r.id == import_id]
        if not found:
            raise KeyError(import_id)
        return found[0]

    def imports(self, project_id: str) -> list[CrawlerImportRecord]:
        """Every import of a project, newest first, with overlap flags."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM crawler_imports WHERE project_id = ? ORDER BY imported_at DESC",
                (project_id,),
            ).fetchall()
            days = conn.execute(
                "SELECT import_id, day FROM crawler_import_days WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        by_day: dict[str, list[str]] = {}
        for d in days:
            by_day.setdefault(str(d["day"]), []).append(str(d["import_id"]))
        overlaps: dict[str, set[str]] = {}
        for owners in by_day.values():
            for owner in owners:
                overlaps.setdefault(owner, set()).update(o for o in owners if o != owner)
        return [
            CrawlerImportRecord(
                id=str(r["id"]),
                imported_at=datetime.fromisoformat(str(r["imported_at"])),
                format=str(r["format"]),
                lines=int(r["lines"]),
                parsed=int(r["parsed"]),
                matched=int(r["matched"]),
                span_from=date.fromisoformat(str(r["span_from"])) if r["span_from"] else None,
                span_to=date.fromisoformat(str(r["span_to"])) if r["span_to"] else None,
                verification_basis=str(r["verification_basis"]),
                sampled=bool(r["sampled"]),
                note=str(r["note"] or ""),
                purged_at=datetime.fromisoformat(str(r["purged_at"])) if r["purged_at"] else None,
                overlaps=sorted(overlaps.get(str(r["id"]), ())),
            )
            for r in rows
        ]

    def winners(self, project_id: str, since: date, until: date) -> dict[str, str]:
        """For each covered day, the import whose data counts (most lines, then newest)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT d.day, d.import_id, d.lines, i.imported_at FROM crawler_import_days d "
                "JOIN crawler_imports i ON i.id = d.import_id "
                "WHERE d.project_id = ? AND d.day BETWEEN ? AND ? "
                "ORDER BY d.day, d.lines DESC, i.imported_at DESC",
                (project_id, since.isoformat(), until.isoformat()),
            ).fetchall()
        chosen: dict[str, str] = {}
        for r in rows:
            chosen.setdefault(str(r["day"]), str(r["import_id"]))
        return chosen

    def hits(self, project_id: str, since: date, until: date) -> list[HitRow]:
        """Aggregate rows in the window, one import per day."""
        chosen = self.winners(project_id, since, until)
        if not chosen:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM crawler_hits WHERE project_id = ? AND day BETWEEN ? AND ?",
                (project_id, since.isoformat(), until.isoformat()),
            ).fetchall()
        out: list[HitRow] = []
        for r in rows:
            if chosen.get(str(r["day"])) != str(r["import_id"]):
                continue
            out.append(
                AggregateRow(
                    day=str(r["day"]),
                    bot=str(r["bot"]),
                    url_key=str(r["url_key"]),
                    hits=int(r["hits"]),
                    s2xx=int(r["s2xx"]),
                    s304=int(r["s304"]),
                    s3xx=int(r["s3xx"]),
                    s4xx=int(r["s4xx"]),
                    s5xx=int(r["s5xx"]),
                    blocked=int(r["blocked"]),
                    verified=int(r["verified_hits"]) if r["verified_hits"] is not None else None,
                    first_seen=_dt(r["first_seen"]),
                    last_seen=_dt(r["last_seen"]),
                )
            )
        return out

    def stealth(self, project_id: str, since: date, until: date) -> dict[str, int]:
        """In-range fetches with no crawler user agent, per vendor, one import per day."""
        chosen = self.winners(project_id, since, until)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT import_id, day, vendor, hits FROM crawler_stealth "
                "WHERE project_id = ? AND day BETWEEN ? AND ?",
                (project_id, since.isoformat(), until.isoformat()),
            ).fetchall()
        out: dict[str, int] = {}
        for r in rows:
            if chosen.get(str(r["day"])) == str(r["import_id"]):
                out[str(r["vendor"])] = out.get(str(r["vendor"]), 0) + int(r["hits"])
        return out


def _dt(value: object) -> datetime | None:
    return datetime.fromisoformat(str(value)) if value else None
