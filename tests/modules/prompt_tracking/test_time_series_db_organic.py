"""Tests for organic-rank persistence and velocity in the time-series store."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from src.modules.prompt_tracking.schemas import (
    DecisionStage,
    MasterPromptRecord,
    OrganicRankSnapshot,
    PromptType,
    RankQueryKind,
    SearchIntent,
    Verdict,
    prompt_id_for,
)
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB

NOW = datetime(2026, 9, 16, 12, tzinfo=UTC)
LOB = "Procurement Software"
TEXT = "What is procurement software?"
PROMPT_ID = prompt_id_for(LOB, TEXT)


@pytest.fixture
def db(tmp_path) -> TimeSeriesDB:
    store = TimeSeriesDB(tmp_path / "t.sqlite")
    store.upsert_prompt(
        MasterPromptRecord(
            prompt_id=PROMPT_ID,
            lob=LOB,
            subtopic="Procurement Software",
            core_keyword="procurement software",
            search_volume=5400,
            prompt_text=TEXT,
            search_intent=SearchIntent.INFORMATIONAL,
            decision_stage=DecisionStage.AWARENESS,
            prompt_type=PromptType.NON_BRANDED,
            web_triggers=True,
            verdict=Verdict.KEEP,
            verdict_reason="x",
        ),
        "GEP",
    )
    return store


def _snap(
    *, kind: RankQueryKind, at: datetime, position: int | None, url: str | None = None
) -> OrganicRankSnapshot:
    return OrganicRankSnapshot(
        query="procurement software" if kind is RankQueryKind.KEYWORD else TEXT,
        query_kind=kind,
        device="mobile",
        captured_at=at,
        samples=2,
        client_position=position,
        client_url=url,
        top_domains=["coupa.com", "gep.com"],
        competitor_positions={"coupa.com": 1},
    )


def test_record_and_history_round_trip(db, tmp_path):
    older = _snap(kind=RankQueryKind.KEYWORD, at=NOW - timedelta(days=1), position=None)
    newer = _snap(kind=RankQueryKind.KEYWORD, at=NOW, position=3, url="https://gep.com/p")
    db.record_organic(PROMPT_ID, older, "run1")
    db.record_organic(PROMPT_ID, newer, "run2")
    db.record_organic(PROMPT_ID, _snap(kind=RankQueryKind.PROMPT, at=NOW, position=8), "run2")

    history = db.organic_history(PROMPT_ID, RankQueryKind.KEYWORD)
    assert [s.captured_at for s in history] == [NOW, NOW - timedelta(days=1)]
    assert history[0] == newer
    assert history[1].client_position is None
    assert history[1].client_url is None
    assert db.organic_history(PROMPT_ID, RankQueryKind.KEYWORD, limit=1) == [newer]
    assert len(db.organic_history(PROMPT_ID, RankQueryKind.PROMPT)) == 1

    conn = sqlite3.connect(tmp_path / "t.sqlite")
    try:
        rows = conn.execute(
            "SELECT run_id, query_kind FROM organic_snapshots ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("run1", "KEYWORD"), ("run2", "KEYWORD"), ("run2", "PROMPT")]


def test_velocity_requires_two_windows(db):
    assert db.organic_velocity(PROMPT_ID, RankQueryKind.PROMPT) is None
    db.record_organic(PROMPT_ID, _snap(kind=RankQueryKind.PROMPT, at=NOW, position=5), "r")
    assert db.organic_velocity(PROMPT_ID, RankQueryKind.PROMPT) is None


def test_velocity_compares_best_position_window_over_window(db):
    for days, position in ((0, 4), (10, 6), (40, 9), (50, 12)):
        db.record_organic(
            PROMPT_ID,
            _snap(kind=RankQueryKind.PROMPT, at=NOW - timedelta(days=days), position=position),
            "r",
        )
    report = db.organic_velocity(PROMPT_ID, RankQueryKind.PROMPT, window_days=30)
    assert report is not None
    assert report.current_best_position == 4
    assert report.previous_best_position == 9
    assert report.position_delta == 5
    assert report.window_days == 30


def test_velocity_delta_is_none_when_a_window_has_no_ranking(db):
    db.record_organic(PROMPT_ID, _snap(kind=RankQueryKind.KEYWORD, at=NOW, position=None), "r")
    db.record_organic(
        PROMPT_ID,
        _snap(kind=RankQueryKind.KEYWORD, at=NOW - timedelta(days=40), position=7),
        "r",
    )
    report = db.organic_velocity(PROMPT_ID, RankQueryKind.KEYWORD)
    assert report is not None
    assert report.current_best_position is None
    assert report.previous_best_position == 7
    assert report.position_delta is None
