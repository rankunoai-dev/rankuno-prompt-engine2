"""Tests for the SQLite time-series store and window-over-window velocity."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from src.integrations.schemas import Engine
from src.modules.prompt_tracking.schemas import (
    CitationSnapshot,
    DecisionStage,
    MasterPromptRecord,
    PromptType,
    SearchIntent,
    TrackerRunSummary,
    Verdict,
    prompt_id_for,
)
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
LOB = "Procurement Software"


def _record(text: str = "What is procurement software?", lob: str = LOB) -> MasterPromptRecord:
    return MasterPromptRecord(
        prompt_id=prompt_id_for(lob, text),
        lob=lob,
        subtopic="Procurement Software",
        core_keyword="procurement software",
        search_volume=100,
        prompt_text=text,
        search_intent=SearchIntent.INFORMATIONAL,
        decision_stage=DecisionStage.AWARENESS,
        prompt_type=PromptType.NON_BRANDED,
        web_triggers=True,
        mapped_url="https://www.gep.com/software/procurement-software",
        verdict=Verdict.KEEP,
        verdict_reason="test",
    )


def _snapshot(
    captured_at: datetime,
    *,
    rate: float = 0.5,
    best_rank: int | None = 2,
    engine: Engine = Engine.GEMINI,
) -> CitationSnapshot:
    return CitationSnapshot(
        engine=engine,
        model="stub-model",
        captured_at=captured_at,
        samples=4,
        failed_samples=1,
        web_trigger_rate=0.75,
        client_cited_samples=2,
        client_citation_rate=rate,
        client_cited=rate >= 0.5,
        client_best_rank=best_rank,
        client_mean_rank=float(best_rank) + 0.5 if best_rank else None,
        cited_domains=["coupa.com", "gep.com"],
        competitor_citations={"coupa.com": 1},
        answer_excerpt="Procurement software helps...",
    )


@pytest.fixture
def db(tmp_path) -> TimeSeriesDB:
    return TimeSeriesDB(tmp_path / "t.sqlite")


def _query(tmp_path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(tmp_path / "t.sqlite")
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


class TestConstruction:
    def test_creates_parent_directory_and_schema(self, tmp_path):
        path = tmp_path / "nested" / "deeper" / "t.sqlite"
        TimeSeriesDB(path)
        assert path.exists()
        conn = sqlite3.connect(path)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        finally:
            conn.close()
        assert {"prompts", "snapshots", "runs"} <= tables

    def test_reopening_is_idempotent(self, tmp_path):
        TimeSeriesDB(tmp_path / "t.sqlite")
        TimeSeriesDB(tmp_path / "t.sqlite")


class TestUpsertPrompt:
    def test_second_upsert_updates_in_place(self, db, tmp_path):
        record = _record()
        db.upsert_prompt(record, "GEP")
        updated = record.model_copy(update={"search_volume": 999, "mapped_url": None})
        db.upsert_prompt(updated, "GEP")
        rows = _query(tmp_path, "SELECT search_volume, mapped_url, brand_name FROM prompts")
        assert rows == [(999, None, "GEP")]

    def test_first_seen_is_preserved_and_last_seen_refreshed(self, db, tmp_path):
        db.upsert_prompt(_record(), "GEP")
        first = _query(tmp_path, "SELECT first_seen, last_seen FROM prompts")[0]
        db.upsert_prompt(_record(), "GEP")
        second = _query(tmp_path, "SELECT first_seen, last_seen FROM prompts")[0]
        assert second[0] == first[0]
        assert second[1] >= first[1]

    def test_snapshot_requires_a_known_prompt(self, db):
        with pytest.raises(sqlite3.IntegrityError):
            db.record_snapshot("0000000000000000", _snapshot(NOW), "run-1")


class TestSnapshotHistory:
    def test_round_trips_every_field(self, db):
        record = _record()
        db.upsert_prompt(record, "GEP")
        snap = _snapshot(NOW)
        db.record_snapshot(record.prompt_id, snap, "run-1")
        history = db.history(record.prompt_id, Engine.GEMINI)
        assert history == [snap]
        assert history[0].cited_domains == ["coupa.com", "gep.com"]
        assert history[0].competitor_citations == {"coupa.com": 1}

    def test_round_trips_none_ranks(self, db):
        record = _record()
        db.upsert_prompt(record, "GEP")
        snap = _snapshot(NOW, rate=0.0, best_rank=None)
        db.record_snapshot(record.prompt_id, snap, "run-1")
        stored = db.history(record.prompt_id, Engine.GEMINI)[0]
        assert stored.client_best_rank is None
        assert stored.client_mean_rank is None
        assert stored.client_cited is False

    def test_newest_first_with_limit_and_engine_filter(self, db):
        record = _record()
        db.upsert_prompt(record, "GEP")
        for days in (5, 1, 3):
            db.record_snapshot(record.prompt_id, _snapshot(NOW - timedelta(days=days)), "r")
        db.record_snapshot(record.prompt_id, _snapshot(NOW, engine=Engine.PERPLEXITY), "r")

        history = db.history(record.prompt_id, Engine.GEMINI)
        assert [s.captured_at for s in history] == [
            NOW - timedelta(days=1),
            NOW - timedelta(days=3),
            NOW - timedelta(days=5),
        ]
        assert db.history(record.prompt_id, Engine.GEMINI, limit=1) == history[:1]
        assert len(db.history(record.prompt_id, Engine.PERPLEXITY)) == 1
        assert db.history(record.prompt_id, Engine.CHATGPT_SEARCH) == []


class TestVelocity:
    def test_none_without_any_snapshot(self, db):
        assert db.velocity("0000000000000000", Engine.GEMINI) is None

    def test_none_with_only_the_current_window(self, db):
        record = _record()
        db.upsert_prompt(record, "GEP")
        db.record_snapshot(record.prompt_id, _snapshot(NOW), "r")
        db.record_snapshot(record.prompt_id, _snapshot(NOW - timedelta(days=10)), "r")
        assert db.velocity(record.prompt_id, Engine.GEMINI) is None

    def test_window_over_window(self, db):
        record = _record()
        db.upsert_prompt(record, "GEP")
        current = [
            _snapshot(NOW, rate=1.0, best_rank=1),
            _snapshot(NOW - timedelta(days=10), rate=0.5, best_rank=2),
        ]
        previous = [
            _snapshot(NOW - timedelta(days=40), rate=0.25, best_rank=3),
            _snapshot(NOW - timedelta(days=50), rate=0.0, best_rank=None),
        ]
        for snap in current + previous:
            db.record_snapshot(record.prompt_id, snap, "r")

        report = db.velocity(record.prompt_id, Engine.GEMINI, window_days=30)
        assert report is not None
        assert report.prompt_id == record.prompt_id
        assert report.engine is Engine.GEMINI
        assert report.window_days == 30
        assert report.current_rate == pytest.approx(0.75)
        assert report.previous_rate == pytest.approx(0.125)
        assert report.rate_delta == pytest.approx(0.625)
        assert report.current_best_rank == 1
        assert report.previous_best_rank == 3
        assert report.rank_delta == 2

    def test_rank_delta_none_when_a_window_has_no_rank(self, db):
        record = _record()
        db.upsert_prompt(record, "GEP")
        db.record_snapshot(record.prompt_id, _snapshot(NOW, rate=0.0, best_rank=None), "r")
        db.record_snapshot(
            record.prompt_id, _snapshot(NOW - timedelta(days=40), rate=0.5, best_rank=2), "r"
        )
        report = db.velocity(record.prompt_id, Engine.GEMINI)
        assert report is not None
        assert report.current_best_rank is None
        assert report.previous_best_rank == 2
        assert report.rank_delta is None
        assert report.rate_delta == pytest.approx(-0.5)


class TestRunsAndLookups:
    def test_record_run_persists_header(self, db, tmp_path):
        summary = TrackerRunSummary(
            run_id="run-0123456789",
            lob=LOB,
            brand_name="GEP",
            started_at=NOW,
            finished_at=NOW + timedelta(minutes=5),
            candidates_generated=30,
            candidates_kept=25,
            prompts_selected=20,
            engine_calls=240,
            failed_engine_calls=3,
            estimated_cost_usd=1.25,
            semrush_units=530,
            report_path="reports/x.csv",
        )
        db.record_run(summary)
        db.record_run(summary)  # INSERT OR REPLACE keeps one row.
        rows = _query(
            tmp_path,
            "SELECT run_id, lob, brand_name, prompts_selected, engine_calls, "
            "failed_engine_calls, estimated_cost_usd, semrush_units, report_path FROM runs",
        )
        assert rows == [("run-0123456789", LOB, "GEP", 20, 240, 3, 1.25, 530, "reports/x.csv")]

    def test_prompt_ids_filters_by_lob(self, db):
        a = _record("What is procurement software?", lob=LOB)
        b = _record("What is supply chain software?", lob="Supply Chain")
        c = _record("How much does procurement software cost?", lob=LOB)
        for record in (a, b, c):
            db.upsert_prompt(record, "GEP")
        assert sorted(db.prompt_ids(LOB)) == sorted([a.prompt_id, c.prompt_id])
        assert db.prompt_ids("Supply Chain") == [b.prompt_id]
        assert db.prompt_ids("Nothing") == []
