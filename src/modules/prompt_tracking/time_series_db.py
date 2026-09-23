"""SQLite time-series store for prompts, citation snapshots and runs.

SQLite (standard library) was chosen over Postgres for the first client: one
writer, a few thousand rows a month, zero infrastructure. The schema is plain
enough to migrate when a second tenant arrives (see ADR 0003).

Velocity is computed as *window over window*: the mean citation rate and best
rank of snapshots in the most recent `window_days` versus the window before it.
That is what "month-over-month" means once every prompt is sampled repeatedly.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from src.core.logger import get_logger
from src.core.sqlite import connect
from src.integrations.schemas import Citation, CitationClaim, Engine, OrganicResult, SourceSnippet
from src.modules.prompt_tracking.schemas import (
    AnswerSample,
    CitationSnapshot,
    MasterPromptRecord,
    MentionJudgement,
    MentionSnippet,
    OrganicRankSnapshot,
    OrganicVelocityReport,
    RankQueryKind,
    TrackerRunSummary,
    VelocityReport,
)

__all__ = ["TimeSeriesDB"]

_logger = get_logger("modules.prompt_tracking.time_series_db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prompts (
    prompt_id      TEXT PRIMARY KEY,
    lob            TEXT NOT NULL,
    brand_name     TEXT NOT NULL,
    prompt_text    TEXT NOT NULL,
    prompt_type    TEXT NOT NULL,
    core_keyword   TEXT NOT NULL,
    subtopic       TEXT NOT NULL,
    search_volume  INTEGER NOT NULL,
    search_intent  TEXT NOT NULL,
    decision_stage TEXT NOT NULL,
    mapped_url     TEXT,
    first_seen     TEXT NOT NULL,
    last_seen      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_id             TEXT NOT NULL REFERENCES prompts(prompt_id),
    run_id                TEXT NOT NULL,
    engine                TEXT NOT NULL,
    model                 TEXT NOT NULL,
    captured_at           TEXT NOT NULL,
    samples               INTEGER NOT NULL,
    failed_samples        INTEGER NOT NULL,
    web_trigger_rate      REAL NOT NULL,
    client_cited_samples  INTEGER NOT NULL,
    client_citation_rate  REAL NOT NULL,
    client_cited          INTEGER NOT NULL,
    client_best_rank      INTEGER,
    client_mean_rank      REAL,
    cited_domains         TEXT NOT NULL,
    competitor_citations  TEXT NOT NULL,
    answer_excerpt        TEXT NOT NULL,
    response_ids          TEXT NOT NULL DEFAULT '[]',
    citation_links        TEXT NOT NULL DEFAULT '[]',
    client_urls           TEXT NOT NULL DEFAULT '[]',
    consulted_urls        TEXT NOT NULL DEFAULT '[]',
    mention_rate          REAL NOT NULL DEFAULT 0.0,
    mention_snippets      TEXT NOT NULL DEFAULT '[]',
    competitor_mentions   TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_snapshots_prompt_engine_time
    ON snapshots(prompt_id, engine, captured_at);
CREATE TABLE IF NOT EXISTS answer_samples (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_id      TEXT NOT NULL REFERENCES prompts(prompt_id),
    run_id         TEXT NOT NULL,
    engine         TEXT NOT NULL,
    model          TEXT NOT NULL,
    captured_at    TEXT NOT NULL,
    response_id    TEXT,
    web_triggered  INTEGER NOT NULL,
    client_cited   INTEGER NOT NULL,
    client_rank    INTEGER,
    cited_domains  TEXT NOT NULL,
    answer_excerpt TEXT NOT NULL,
    citation_links TEXT NOT NULL DEFAULT '[]',
    consulted_urls TEXT NOT NULL DEFAULT '[]',
    mention_detected INTEGER NOT NULL DEFAULT 0,
    mentions       TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_samples_engine_time ON answer_samples(engine, captured_at);
-- `samples_for` filters on prompt_id and orders by captured_at; without this the one
-- per-prompt read in the API is a full table scan.
CREATE INDEX IF NOT EXISTS idx_samples_prompt_time ON answer_samples(prompt_id, captured_at);
CREATE TABLE IF NOT EXISTS jobs (
    job_name     TEXT PRIMARY KEY,
    interval     TEXT NOT NULL,
    last_run_at  TEXT,
    last_run_id  TEXT,
    last_status  TEXT,
    next_run_at  TEXT
);
CREATE TABLE IF NOT EXISTS organic_snapshots (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_id            TEXT NOT NULL REFERENCES prompts(prompt_id),
    run_id               TEXT NOT NULL,
    query                TEXT NOT NULL,
    query_kind           TEXT NOT NULL,
    device               TEXT NOT NULL,
    captured_at          TEXT NOT NULL,
    samples              INTEGER NOT NULL,
    client_position      INTEGER,
    client_url           TEXT,
    top_domains          TEXT NOT NULL,
    competitor_positions TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_organic_prompt_kind_time
    ON organic_snapshots(prompt_id, query_kind, captured_at);
-- One row per (sample, entity, sentence) scored by the judge (ADR 0021). The
-- model and rubric version are on every row; a change never rewrites history.
CREATE TABLE IF NOT EXISTS mention_judgements (
    prompt_id      TEXT NOT NULL,
    run_id         TEXT NOT NULL,
    engine         TEXT NOT NULL,
    captured_at    TEXT NOT NULL,
    entity         TEXT NOT NULL,
    term           TEXT NOT NULL,
    sentence_sha1  TEXT NOT NULL,
    sentence       TEXT NOT NULL,
    status         TEXT NOT NULL,
    polarity       TEXT NOT NULL,
    attributes     TEXT NOT NULL DEFAULT '[]',
    confidence     REAL NOT NULL DEFAULT 0.0,
    model          TEXT NOT NULL,
    rubric_version TEXT NOT NULL,
    judged_at      TEXT NOT NULL,
    PRIMARY KEY (prompt_id, run_id, engine, captured_at, entity, sentence_sha1)
);
CREATE INDEX IF NOT EXISTS idx_judgements_run ON mention_judgements(run_id, engine);
CREATE INDEX IF NOT EXISTS idx_judgements_cache
    ON mention_judgements(sentence_sha1, entity, model, rubric_version);
CREATE TABLE IF NOT EXISTS runs (
    run_id             TEXT PRIMARY KEY,
    lob                TEXT NOT NULL,
    brand_name         TEXT NOT NULL,
    started_at         TEXT NOT NULL,
    finished_at        TEXT NOT NULL,
    prompts_selected   INTEGER NOT NULL,
    engine_calls       INTEGER NOT NULL,
    failed_engine_calls INTEGER NOT NULL,
    estimated_cost_usd REAL NOT NULL,
    semrush_units      INTEGER NOT NULL,
    report_path        TEXT
);
"""


class TimeSeriesDB:
    """Thin, typed wrapper over one SQLite file."""

    def __init__(self, path: Path) -> None:
        """Open (creating if needed) the database at `path`."""
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            _migrate(conn)

    @property
    def path(self) -> Path:
        """Location of the SQLite file."""
        return self._path

    def ping(self) -> None:
        """Prove the store is reachable and writable; raises if it is not.

        Used by the health endpoint so an unmounted or read-only volume fails the
        platform's check instead of passing it.
        """
        with self._connect() as conn:
            conn.execute("SELECT 1 FROM prompts LIMIT 1").fetchall()
            conn.execute("PRAGMA user_version")  # touches the file header for writability

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Connection with the platform pragmas and commit-or-rollback semantics."""
        conn = connect(self._path)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # -- writes ------------------------------------------------------------

    def upsert_prompt(self, record: MasterPromptRecord, brand_name: str) -> None:
        """Insert the prompt or refresh its mutable fields and `last_seen`."""
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO prompts (prompt_id, lob, brand_name, prompt_text, prompt_type,
                    core_keyword, subtopic, search_volume, search_intent, decision_stage,
                    mapped_url, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(prompt_id) DO UPDATE SET
                    search_volume = excluded.search_volume,
                    search_intent = excluded.search_intent,
                    decision_stage = excluded.decision_stage,
                    mapped_url = excluded.mapped_url,
                    last_seen = excluded.last_seen
                """,
                (
                    record.prompt_id,
                    record.lob,
                    brand_name,
                    record.prompt_text,
                    record.prompt_type.value,
                    record.core_keyword,
                    record.subtopic,
                    record.search_volume,
                    record.search_intent.value,
                    record.decision_stage.value,
                    record.mapped_url,
                    now,
                    now,
                ),
            )

    def record_snapshot(self, prompt_id: str, snapshot: CitationSnapshot, run_id: str) -> None:
        """Append one snapshot. History is never updated in place."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO snapshots (prompt_id, run_id, engine, model, captured_at, samples,
                    failed_samples, web_trigger_rate, client_cited_samples, client_citation_rate,
                    client_cited, client_best_rank, client_mean_rank, cited_domains,
                    competitor_citations, answer_excerpt, response_ids, citation_links,
                    client_urls, consulted_urls, mention_rate, mention_snippets,
                    competitor_mentions)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    prompt_id,
                    run_id,
                    snapshot.engine.value,
                    snapshot.model,
                    snapshot.captured_at.isoformat(),
                    snapshot.samples,
                    snapshot.failed_samples,
                    snapshot.web_trigger_rate,
                    snapshot.client_cited_samples,
                    snapshot.client_citation_rate,
                    int(snapshot.client_cited),
                    snapshot.client_best_rank,
                    snapshot.client_mean_rank,
                    json.dumps(snapshot.cited_domains),
                    json.dumps(snapshot.competitor_citations),
                    snapshot.answer_excerpt,
                    json.dumps(snapshot.response_ids),
                    json.dumps([c.model_dump() for c in snapshot.citation_links]),
                    json.dumps(snapshot.client_urls),
                    json.dumps(snapshot.consulted_urls),
                    snapshot.mention_rate,
                    json.dumps([m.model_dump() for m in snapshot.mention_snippets]),
                    json.dumps(snapshot.competitor_mentions),
                ),
            )

    def record_samples(self, prompt_id: str, samples: list[AnswerSample], run_id: str) -> None:
        """Append raw per-answer rows (model, response id, verdict) for one prompt."""
        if not samples:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO answer_samples (prompt_id, run_id, engine, model, captured_at,
                    response_id, web_triggered, client_cited, client_rank, cited_domains,
                    answer_excerpt, citation_links, consulted_urls, mention_detected, mentions,
                    answer_text, search_queries, citation_claims, source_snippets)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        prompt_id,
                        run_id,
                        s.engine.value,
                        s.model,
                        s.captured_at.isoformat(),
                        s.response_id,
                        int(s.web_triggered),
                        int(s.client_cited),
                        s.client_rank,
                        json.dumps(s.cited_domains),
                        s.answer_excerpt,
                        json.dumps([c.model_dump() for c in s.citation_links]),
                        json.dumps(s.consulted_urls),
                        int(s.mention_detected),
                        json.dumps([m.model_dump() for m in s.mentions]),
                        s.answer_text,
                        json.dumps(s.search_queries),
                        json.dumps([c.model_dump() for c in s.citation_claims]),
                        json.dumps([c.model_dump() for c in s.source_snippets]),
                    )
                    for s in samples
                ],
            )

    def record_organic(self, prompt_id: str, snapshot: OrganicRankSnapshot, run_id: str) -> None:
        """Append one organic ranking snapshot."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO organic_snapshots (prompt_id, run_id, query, query_kind, device,
                    captured_at, samples, client_position, client_url, top_domains,
                    competitor_positions, organic_results, paa_questions, related_searches)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    prompt_id,
                    run_id,
                    snapshot.query,
                    snapshot.query_kind.value,
                    snapshot.device,
                    snapshot.captured_at.isoformat(),
                    snapshot.samples,
                    snapshot.client_position,
                    snapshot.client_url,
                    json.dumps(snapshot.top_domains),
                    json.dumps(snapshot.competitor_positions),
                    json.dumps([r.model_dump() for r in snapshot.organic_results]),
                    json.dumps(snapshot.paa_questions),
                    json.dumps(snapshot.related_searches),
                ),
            )

    def samples_for(
        self,
        prompt_id: str,
        *,
        engine: Engine | None = None,
        run_ids: list[str] | None = None,
        limit: int = 500,
    ) -> list[AnswerSample]:
        """Raw answer samples for a prompt, newest first, optionally per engine and runs."""
        clauses = ["prompt_id = ?"]
        params: list[object] = [prompt_id]
        if engine is not None:
            clauses.append("engine = ?")
            params.append(engine.value)
        if run_ids is not None:
            if not run_ids:
                return []
            clauses.append("run_id IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(run_ids))
        params.append(limit)
        where = " AND ".join(clauses)  # every clause is a constant above; values are bound
        query = (
            "SELECT prompt_id, run_id, engine, model, captured_at, response_id, web_triggered, "  # noqa: S608 - constant clauses, bound values
            "client_cited, client_rank, cited_domains, answer_excerpt, citation_links, "
            "consulted_urls, mention_detected, mentions, answer_text, search_queries, "
            "citation_claims, source_snippets FROM answer_samples WHERE " + where
        )
        with self._connect() as conn:
            rows = conn.execute(query + " ORDER BY captured_at DESC LIMIT ?", params).fetchall()
        return [_row_to_sample(row) for row in rows]

    def samples_since(
        self, prompt_ids: list[str], since: datetime, *, limit: int = 5000
    ) -> list[AnswerSample]:
        """Samples for a set of prompts captured on or after `since`, newest first.

        Used by the crawler-log funnel, which needs every citation and consulted
        URL of a project in a window rather than one prompt's history.
        """
        if not prompt_ids:
            return []
        query = (
            "SELECT prompt_id, run_id, engine, model, captured_at, response_id, web_triggered, "
            "client_cited, client_rank, cited_domains, answer_excerpt, citation_links, "
            "consulted_urls, mention_detected, mentions, answer_text, search_queries, "
            "citation_claims, source_snippets FROM answer_samples "
            "WHERE prompt_id IN (SELECT value FROM json_each(?)) AND captured_at >= ? "
            "ORDER BY captured_at DESC LIMIT ?"
        )
        with self._connect() as conn:
            rows = conn.execute(
                query, (json.dumps(prompt_ids), since.isoformat(), limit)
            ).fetchall()
        return [_row_to_sample(row) for row in rows]

    def sample_run_ids(self, prompt_id: str) -> list[str]:
        """Distinct run ids that sampled a prompt, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT run_id, MAX(captured_at) AS at FROM answer_samples WHERE prompt_id = ? "
                "GROUP BY run_id ORDER BY at DESC",
                (prompt_id,),
            ).fetchall()
        return [str(r[0]) for r in rows]

    # -- mention judgements (ADR 0021) ------------------------------------------

    def record_judgements(self, rows: list[MentionJudgement]) -> None:
        """Upsert judgement rows; re-running a crawl's judge step is idempotent."""
        if not rows:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO mention_judgements (prompt_id, run_id, engine,
                    captured_at, entity, term, sentence_sha1, sentence, status, polarity,
                    attributes, confidence, model, rubric_version, judged_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        r.prompt_id,
                        r.run_id,
                        r.engine.value,
                        r.captured_at.isoformat(),
                        r.entity,
                        r.term,
                        r.sentence_sha1,
                        r.sentence,
                        r.status,
                        r.polarity,
                        json.dumps(r.attributes),
                        r.confidence,
                        r.model,
                        r.rubric_version,
                        r.judged_at.isoformat(),
                    )
                    for r in rows
                ],
            )

    def judgements_for(
        self, *, run_ids: list[str], prompt_ids: list[str] | None = None
    ) -> list[MentionJudgement]:
        """Every judgement row from the given runs, optionally for some prompts only."""
        if not run_ids or prompt_ids == []:
            return []
        clauses = ["run_id IN (SELECT value FROM json_each(?))"]
        params: list[object] = [json.dumps(run_ids)]
        if prompt_ids is not None:
            clauses.append("prompt_id IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(prompt_ids))
        where = " AND ".join(clauses)  # constant clauses, bound values
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_JUDGEMENT_COLUMNS} FROM mention_judgements WHERE {where} "  # noqa: S608 - constant clauses, bound values
                "ORDER BY captured_at",
                params,
            ).fetchall()
        return [_row_to_judgement(r) for r in rows]

    def cached_judgements(
        self, keys: list[tuple[str, str]], *, model: str, rubric_version: str
    ) -> dict[tuple[str, str], MentionJudgement]:
        """Earlier `ok` verdicts for (sentence_sha1, entity) under the same model and rubric.

        Engines repeat themselves; an identical sentence scored once is reused
        rather than paid for again, and scores identically by construction.
        """
        if not keys:
            return {}
        sha1s = sorted({k[0] for k in keys})
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_JUDGEMENT_COLUMNS} FROM mention_judgements "  # noqa: S608 - constant columns, bound values
                "WHERE status = 'ok' AND model = ? AND rubric_version = ? "
                "AND sentence_sha1 IN (SELECT value FROM json_each(?))",
                (model, rubric_version, json.dumps(sha1s)),
            ).fetchall()
        wanted = set(keys)
        found: dict[tuple[str, str], MentionJudgement] = {}
        for row in rows:
            judgement = _row_to_judgement(row)
            key = (judgement.sentence_sha1, judgement.entity)
            if key in wanted and key not in found:
                found[key] = judgement
        return found

    def capture_coverage(self, prompt_id: str) -> dict[str, object]:
        """How many of a prompt's samples carry each rich-capture layer.

        Counted in SQL: the alternative is pulling every sample's full answer text
        just to test it for emptiness.
        """
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS samples,
                       SUM(CASE WHEN COALESCE(answer_text, '') != '' THEN 1 ELSE 0 END),
                       SUM(CASE WHEN COALESCE(search_queries, '[]') NOT IN ('', '[]')
                                THEN 1 ELSE 0 END),
                       SUM(CASE WHEN COALESCE(citation_claims, '[]') NOT IN ('', '[]')
                                THEN 1 ELSE 0 END),
                       SUM(CASE WHEN COALESCE(source_snippets, '[]') NOT IN ('', '[]')
                                THEN 1 ELSE 0 END),
                       MIN(captured_at), MAX(captured_at)
                FROM answer_samples WHERE prompt_id = ?
                """,
                (prompt_id,),
            ).fetchone()
        return {
            "samples": int(row[0] or 0),
            "with_answer_text": int(row[1] or 0),
            "with_search_queries": int(row[2] or 0),
            "with_citation_claims": int(row[3] or 0),
            "with_source_snippets": int(row[4] or 0),
            "first_captured_at": str(row[5]) if row[5] else None,
            "last_captured_at": str(row[6]) if row[6] else None,
        }

    def prompt_records(self, prompt_ids: list[str]) -> dict[str, dict[str, object]]:
        """Volume, subtopic, keyword and mapped URL per prompt id (for insight weighting)."""
        if not prompt_ids:
            return {}
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT prompt_id, search_volume, subtopic, core_keyword, mapped_url, prompt_text "
                "FROM prompts WHERE prompt_id IN (SELECT value FROM json_each(?))",
                (json.dumps(prompt_ids),),
            ).fetchall()
        return {
            str(r[0]): {
                "search_volume": int(r[1] or 0),
                "subtopic": str(r[2] or ""),
                "core_keyword": str(r[3] or ""),
                "mapped_url": str(r[4]) if r[4] else None,
                "prompt_text": str(r[5] or ""),
            }
            for r in rows
        }

    def record_run(self, summary: TrackerRunSummary) -> None:
        """Persist the run header for auditability."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO runs (run_id, lob, brand_name, started_at, finished_at,
                    prompts_selected, engine_calls, failed_engine_calls, estimated_cost_usd,
                    semrush_units, report_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    summary.run_id,
                    summary.lob,
                    summary.brand_name,
                    summary.started_at.isoformat(),
                    summary.finished_at.isoformat(),
                    summary.prompts_selected,
                    summary.engine_calls,
                    summary.failed_engine_calls,
                    summary.estimated_cost_usd,
                    summary.semrush_units,
                    summary.report_path,
                ),
            )
        _logger.info("run_recorded", extra={"run_id": summary.run_id})

    # -- reads -------------------------------------------------------------

    def history(
        self, prompt_id: str, engine: Engine, *, limit: int = 100
    ) -> list[CitationSnapshot]:
        """Snapshots for one prompt on one engine, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT engine, model, captured_at, samples, failed_samples, web_trigger_rate,
                       client_cited_samples, client_citation_rate, client_cited, client_best_rank,
                       client_mean_rank, cited_domains, competitor_citations, answer_excerpt,
                       response_ids, citation_links, client_urls, consulted_urls, mention_rate,
                       mention_snippets, competitor_mentions
                FROM snapshots WHERE prompt_id = ? AND engine = ?
                ORDER BY captured_at DESC LIMIT ?
                """,
                (prompt_id, engine.value, limit),
            ).fetchall()
        return [_row_to_snapshot(row) for row in rows]

    def velocity(
        self, prompt_id: str, engine: Engine, *, window_days: int = 30
    ) -> VelocityReport | None:
        """Window-over-window change, or None without a previous window."""
        snapshots = self.history(prompt_id, engine, limit=10_000)
        if not snapshots:
            return None
        newest = snapshots[0].captured_at
        window = timedelta(days=window_days)
        current = [s for s in snapshots if s.captured_at > newest - window]
        previous = [s for s in snapshots if newest - 2 * window < s.captured_at <= newest - window]
        if not previous:
            return None

        cur_rate = sum(s.client_citation_rate for s in current) / len(current)
        prev_rate = sum(s.client_citation_rate for s in previous) / len(previous)
        cur_rank = _best_rank(current)
        prev_rank = _best_rank(previous)
        return VelocityReport(
            prompt_id=prompt_id,
            engine=engine,
            window_days=window_days,
            current_rate=round(cur_rate, 4),
            previous_rate=round(prev_rate, 4),
            rate_delta=round(cur_rate - prev_rate, 4),
            current_best_rank=cur_rank,
            previous_best_rank=prev_rank,
            rank_delta=(prev_rank - cur_rank) if cur_rank and prev_rank else None,
        )

    def organic_history(
        self, prompt_id: str, kind: RankQueryKind, *, limit: int = 100
    ) -> list[OrganicRankSnapshot]:
        """Organic snapshots for one prompt and query kind, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT query, query_kind, device, captured_at, samples, client_position,
                       client_url, top_domains, competitor_positions, organic_results,
                       paa_questions, related_searches
                FROM organic_snapshots WHERE prompt_id = ? AND query_kind = ?
                ORDER BY captured_at DESC LIMIT ?
                """,
                (prompt_id, kind.value, limit),
            ).fetchall()
        return [_row_to_organic(row) for row in rows]

    def organic_velocity(
        self, prompt_id: str, kind: RankQueryKind, *, window_days: int = 30
    ) -> OrganicVelocityReport | None:
        """Best organic position, latest window versus the one before it."""
        snapshots = self.organic_history(prompt_id, kind, limit=10_000)
        if not snapshots:
            return None
        newest = snapshots[0].captured_at
        window = timedelta(days=window_days)
        current = [s for s in snapshots if s.captured_at > newest - window]
        previous = [s for s in snapshots if newest - 2 * window < s.captured_at <= newest - window]
        if not previous:
            return None
        cur = _best_position(current)
        prev = _best_position(previous)
        return OrganicVelocityReport(
            prompt_id=prompt_id,
            query_kind=kind,
            window_days=window_days,
            current_best_position=cur,
            previous_best_position=prev,
            position_delta=(prev - cur) if cur is not None and prev is not None else None,
        )

    def runs_for(self, lob: str, *, limit: int = 50) -> list[dict[str, object]]:
        """Run headers for an LOB, newest first, as plain dicts for the API layer."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT run_id, started_at, finished_at, prompts_selected, engine_calls,
                       failed_engine_calls, estimated_cost_usd, report_path
                FROM runs WHERE lob = ? ORDER BY started_at DESC LIMIT ?
                """,
                (lob, limit),
            ).fetchall()
        keys = (
            "run_id",
            "started_at",
            "finished_at",
            "prompts_selected",
            "engine_calls",
            "failed_engine_calls",
            "estimated_cost_usd",
            "report_path",
        )
        return [dict(zip(keys, row, strict=True)) for row in rows]

    def prompt_ids(self, lob: str) -> list[str]:
        """All prompt ids tracked for an LOB."""
        with self._connect() as conn:
            rows = conn.execute("SELECT prompt_id FROM prompts WHERE lob = ?", (lob,)).fetchall()
        return [str(r[0]) for r in rows]

    def last_model(self, engine: Engine, *, exclude_run_id: str) -> str | None:
        """Model string of the most recent stored answer for `engine` outside one run."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT model FROM answer_samples WHERE engine = ? AND run_id != ?
                ORDER BY captured_at DESC LIMIT 1
                """,
                (engine.value, exclude_run_id),
            ).fetchone()
        return str(row[0]) if row else None

    # -- scheduler state ---------------------------------------------------

    def job_state(self, job_name: str) -> dict[str, str | None] | None:
        """Stored scheduler state for a job, or None if it never ran."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT interval, last_run_at, last_run_id, last_status, next_run_at "
                "FROM jobs WHERE job_name = ?",
                (job_name,),
            ).fetchone()
        if row is None:
            return None
        keys = ("interval", "last_run_at", "last_run_id", "last_status", "next_run_at")
        return {k: (str(v) if v is not None else None) for k, v in zip(keys, row, strict=True)}

    def record_job_run(
        self,
        job_name: str,
        *,
        interval: str,
        last_run_at: datetime,
        last_run_id: str | None,
        last_status: str,
        next_run_at: datetime,
    ) -> None:
        """Upsert a job's scheduler state after a run attempt."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs (job_name, interval, last_run_at, last_run_id, last_status,
                    next_run_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_name) DO UPDATE SET
                    interval = excluded.interval,
                    last_run_at = excluded.last_run_at,
                    last_run_id = excluded.last_run_id,
                    last_status = excluded.last_status,
                    next_run_at = excluded.next_run_at
                """,
                (
                    job_name,
                    interval,
                    last_run_at.isoformat(),
                    last_run_id,
                    last_status,
                    next_run_at.isoformat(),
                ),
            )


def _best_rank(snapshots: list[CitationSnapshot]) -> int | None:
    ranks = [s.client_best_rank for s in snapshots if s.client_best_rank is not None]
    return min(ranks) if ranks else None


def _best_position(snapshots: list[OrganicRankSnapshot]) -> int | None:
    positions = [s.client_position for s in snapshots if s.client_position is not None]
    return min(positions) if positions else None


def _row_to_sample(row: tuple[Any, ...]) -> AnswerSample:
    """Map an `answer_samples` row (column order as selected in `samples_for`) to the contract."""
    return AnswerSample(
        prompt_id=str(row[0]),
        run_id=str(row[1] or ""),
        engine=Engine(str(row[2])),
        model=str(row[3]),
        captured_at=datetime.fromisoformat(str(row[4])),
        response_id=str(row[5]) if row[5] is not None else None,
        web_triggered=bool(row[6]),
        client_cited=bool(row[7]),
        client_rank=int(str(row[8])) if row[8] is not None else None,
        cited_domains=json.loads(str(row[9]) or "[]"),
        answer_excerpt=str(row[10] or ""),
        citation_links=[Citation.model_validate(c) for c in json.loads(str(row[11]) or "[]")],
        consulted_urls=json.loads(str(row[12]) or "[]"),
        mention_detected=bool(row[13]),
        mentions=[MentionSnippet.model_validate(m) for m in json.loads(str(row[14]) or "[]")],
        answer_text=str(row[15] or ""),
        search_queries=json.loads(str(row[16]) or "[]"),
        citation_claims=[CitationClaim.model_validate(c) for c in json.loads(str(row[17]) or "[]")],
        source_snippets=[SourceSnippet.model_validate(c) for c in json.loads(str(row[18]) or "[]")],
    )


_JUDGEMENT_COLUMNS = (
    "prompt_id, run_id, engine, captured_at, entity, term, sentence_sha1, sentence, status, "
    "polarity, attributes, confidence, model, rubric_version, judged_at"
)


def _row_to_judgement(row: tuple[Any, ...]) -> MentionJudgement:
    """Map a `mention_judgements` row (column order as `_JUDGEMENT_COLUMNS`) to the contract."""
    return MentionJudgement(
        prompt_id=str(row[0]),
        run_id=str(row[1]),
        engine=Engine(str(row[2])),
        captured_at=datetime.fromisoformat(str(row[3])),
        entity=str(row[4]),
        term=str(row[5]),
        sentence_sha1=str(row[6]),
        sentence=str(row[7]),
        status=str(row[8]),
        polarity=str(row[9]),
        attributes=json.loads(str(row[10]) or "[]"),
        confidence=float(row[11] or 0.0),
        model=str(row[12]),
        rubric_version=str(row[13]),
        judged_at=datetime.fromisoformat(str(row[14])),
    )


def _row_to_organic(row: tuple[Any, ...]) -> OrganicRankSnapshot:
    """Map an `organic_snapshots` row (column order as selected) to the contract."""
    extra = row[9:12] if len(row) >= 12 else ("[]", "[]", "[]")
    return OrganicRankSnapshot(
        query=str(row[0]),
        query_kind=RankQueryKind(str(row[1])),
        device=str(row[2]),
        captured_at=datetime.fromisoformat(str(row[3])),
        samples=int(str(row[4])),
        client_position=int(str(row[5])) if row[5] is not None else None,
        client_url=str(row[6]) if row[6] is not None else None,
        top_domains=json.loads(str(row[7])),
        competitor_positions=json.loads(str(row[8])),
        organic_results=[
            OrganicResult.model_validate(r) for r in json.loads(str(extra[0]) or "[]")
        ],
        paa_questions=json.loads(str(extra[1]) or "[]"),
        related_searches=json.loads(str(extra[2]) or "[]"),
    )


def _row_to_snapshot(row: tuple[object, ...]) -> CitationSnapshot:
    """Rehydrate a snapshot row; JSON columns are decoded here and only here."""
    return CitationSnapshot(
        engine=Engine(str(row[0])),
        model=str(row[1]),
        captured_at=datetime.fromisoformat(str(row[2])),
        samples=int(str(row[3])),
        failed_samples=int(str(row[4])),
        web_trigger_rate=float(str(row[5])),
        client_cited_samples=int(str(row[6])),
        client_citation_rate=float(str(row[7])),
        client_cited=bool(int(str(row[8]))),
        client_best_rank=int(str(row[9])) if row[9] is not None else None,
        client_mean_rank=float(str(row[10])) if row[10] is not None else None,
        cited_domains=list(json.loads(str(row[11]))),
        competitor_citations=dict(json.loads(str(row[12]))),
        answer_excerpt=str(row[13]),
        response_ids=list(json.loads(str(row[14]))),
        citation_links=[Citation.model_validate(c) for c in json.loads(str(row[15]))],
        client_urls=list(json.loads(str(row[16]))),
        consulted_urls=list(json.loads(str(row[17]))),
        mention_rate=float(str(row[18])),
        mention_detected=float(str(row[18])) > 0.0,
        mention_snippets=[MentionSnippet.model_validate(m) for m in json.loads(str(row[19]))],
        competitor_mentions=dict(json.loads(str(row[20]))),
    )


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a table was first created.

    `CREATE TABLE IF NOT EXISTS` never alters an existing table, so each
    addition is listed here and applied idempotently.
    """
    additions = {
        ("snapshots", "response_ids"): "TEXT NOT NULL DEFAULT '[]'",
        ("snapshots", "citation_links"): "TEXT NOT NULL DEFAULT '[]'",
        ("snapshots", "client_urls"): "TEXT NOT NULL DEFAULT '[]'",
        ("snapshots", "consulted_urls"): "TEXT NOT NULL DEFAULT '[]'",
        ("snapshots", "mention_rate"): "REAL NOT NULL DEFAULT 0.0",
        ("snapshots", "mention_snippets"): "TEXT NOT NULL DEFAULT '[]'",
        ("snapshots", "competitor_mentions"): "TEXT NOT NULL DEFAULT '{}'",
        ("answer_samples", "citation_links"): "TEXT NOT NULL DEFAULT '[]'",
        ("answer_samples", "consulted_urls"): "TEXT NOT NULL DEFAULT '[]'",
        ("answer_samples", "mention_detected"): "INTEGER NOT NULL DEFAULT 0",
        ("answer_samples", "mentions"): "TEXT NOT NULL DEFAULT '[]'",
        ("answer_samples", "answer_text"): "TEXT NOT NULL DEFAULT ''",
        ("answer_samples", "search_queries"): "TEXT NOT NULL DEFAULT '[]'",
        ("answer_samples", "citation_claims"): "TEXT NOT NULL DEFAULT '[]'",
        ("answer_samples", "source_snippets"): "TEXT NOT NULL DEFAULT '[]'",
        ("organic_snapshots", "organic_results"): "TEXT NOT NULL DEFAULT '[]'",
        ("organic_snapshots", "paa_questions"): "TEXT NOT NULL DEFAULT '[]'",
        ("organic_snapshots", "related_searches"): "TEXT NOT NULL DEFAULT '[]'",
    }
    for (table, column), definition in additions.items():
        existing = {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}  # noqa: S608
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")  # noqa: S608
            _logger.info("schema_migrated", extra={"table": table, "column": column})
