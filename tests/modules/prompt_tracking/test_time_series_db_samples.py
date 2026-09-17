"""Tests for answer samples, model lookup, scheduler state and schema migration."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from src.integrations.schemas import Engine
from src.modules.prompt_tracking.schemas import (
    AnswerSample,
    DecisionStage,
    MasterPromptRecord,
    PromptType,
    SearchIntent,
    Verdict,
    prompt_id_for,
)
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB

NOW = datetime(2026, 9, 16, 12, tzinfo=UTC)
LOB = "Procurement Software"
TEXT = "What is procurement software?"
PROMPT_ID = prompt_id_for(LOB, TEXT)


def _record() -> MasterPromptRecord:
    return MasterPromptRecord(
        prompt_id=PROMPT_ID,
        lob=LOB,
        subtopic="S",
        core_keyword="procurement software",
        search_volume=1,
        prompt_text=TEXT,
        search_intent=SearchIntent.INFORMATIONAL,
        decision_stage=DecisionStage.AWARENESS,
        prompt_type=PromptType.NON_BRANDED,
        web_triggers=True,
        verdict=Verdict.KEEP,
        verdict_reason="x",
    )


def _sample(*, model: str, at: datetime, response_id: str | None = "r") -> AnswerSample:
    return AnswerSample(
        prompt_id=PROMPT_ID,
        engine=Engine.GEMINI,
        model=model,
        captured_at=at,
        response_id=response_id,
        web_triggered=True,
        client_cited=True,
        client_rank=2,
        cited_domains=["gep.com"],
        answer_excerpt="text",
    )


@pytest.fixture
def db(tmp_path) -> TimeSeriesDB:
    store = TimeSeriesDB(tmp_path / "t.sqlite")
    store.upsert_prompt(_record(), "GEP")
    return store


def test_record_samples_and_last_model(db):
    assert db.last_model(Engine.GEMINI, exclude_run_id="x") is None
    db.record_samples(PROMPT_ID, [_sample(model="g-1", at=NOW - timedelta(days=1))], "run1")
    db.record_samples(PROMPT_ID, [_sample(model="g-2", at=NOW, response_id=None)], "run2")
    db.record_samples(PROMPT_ID, [], "run3")  # no-op

    assert db.last_model(Engine.GEMINI, exclude_run_id="run2") == "g-1"
    assert db.last_model(Engine.GEMINI, exclude_run_id="other") == "g-2"
    assert db.last_model(Engine.PERPLEXITY, exclude_run_id="other") is None


def test_job_state_round_trip(db):
    assert db.job_state("nightly") is None
    db.record_job_run(
        "nightly",
        interval="daily",
        last_run_at=NOW,
        last_run_id="abc",
        last_status="success",
        next_run_at=NOW + timedelta(days=1),
    )
    state = db.job_state("nightly")
    assert state == {
        "interval": "daily",
        "last_run_at": NOW.isoformat(),
        "last_run_id": "abc",
        "last_status": "success",
        "next_run_at": (NOW + timedelta(days=1)).isoformat(),
    }
    db.record_job_run(
        "nightly",
        interval="weekly",
        last_run_at=NOW + timedelta(days=1),
        last_run_id=None,
        last_status="failed",
        next_run_at=NOW + timedelta(days=8),
    )
    state = db.job_state("nightly")
    assert state is not None
    assert state["interval"] == "weekly"
    assert state["last_run_id"] is None
    assert state["last_status"] == "failed"


def test_migration_adds_response_ids_to_a_pre_existing_snapshots_table(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE prompts (prompt_id TEXT PRIMARY KEY, lob TEXT NOT NULL,
            brand_name TEXT NOT NULL,
            prompt_text TEXT NOT NULL, prompt_type TEXT NOT NULL, core_keyword TEXT NOT NULL,
            subtopic TEXT NOT NULL, search_volume INTEGER NOT NULL, search_intent TEXT NOT NULL,
            decision_stage TEXT NOT NULL, mapped_url TEXT, first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL);
        CREATE TABLE snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, prompt_id TEXT NOT NULL,
            run_id TEXT NOT NULL, engine TEXT NOT NULL, model TEXT NOT NULL,
            captured_at TEXT NOT NULL, samples INTEGER NOT NULL, failed_samples INTEGER NOT NULL,
            web_trigger_rate REAL NOT NULL, client_cited_samples INTEGER NOT NULL,
            client_citation_rate REAL NOT NULL, client_cited INTEGER NOT NULL,
            client_best_rank INTEGER, client_mean_rank REAL, cited_domains TEXT NOT NULL,
            competitor_citations TEXT NOT NULL, answer_excerpt TEXT NOT NULL);
        """
    )
    conn.commit()
    conn.close()

    store = TimeSeriesDB(path)  # migrates
    columns = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(snapshots)")}
    assert "response_ids" in columns
    store.upsert_prompt(_record(), "GEP")
    assert store.history(PROMPT_ID, Engine.GEMINI) == []
    TimeSeriesDB(path)  # idempotent
