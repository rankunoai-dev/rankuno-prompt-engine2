"""Round-trip of links, consulted URLs and mentions through the store, incl. migration."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from src.integrations.schemas import Citation, Engine
from src.modules.prompt_tracking.schemas import (
    AnswerSample,
    CitationSnapshot,
    DecisionStage,
    MasterPromptRecord,
    MentionSnippet,
    PromptType,
    SearchIntent,
    Verdict,
    prompt_id_for,
)
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB

NOW = datetime(2026, 9, 16, 12, tzinfo=UTC)
LOB = "L"
TEXT = "What is procurement software?"
PID = prompt_id_for(LOB, TEXT)
GEP = "https://www.gep.com/x"


def _record() -> MasterPromptRecord:
    return MasterPromptRecord(
        prompt_id=PID,
        lob=LOB,
        subtopic="S",
        core_keyword="k",
        search_volume=0,
        prompt_text=TEXT,
        search_intent=SearchIntent.INFORMATIONAL,
        decision_stage=DecisionStage.AWARENESS,
        prompt_type=PromptType.NON_BRANDED,
        web_triggers=True,
        verdict=Verdict.KEEP,
        verdict_reason="x",
    )


def _snapshot() -> CitationSnapshot:
    return CitationSnapshot(
        engine=Engine.PERPLEXITY,
        model="sonar-pro",
        captured_at=NOW,
        samples=1,
        web_trigger_rate=1.0,
        client_cited_samples=1,
        client_citation_rate=1.0,
        client_cited=True,
        client_best_rank=1,
        client_mean_rank=1.0,
        cited_domains=["gep.com"],
        citation_links=[Citation(url=GEP, domain="gep.com", position=1, title="GEP")],
        client_urls=[GEP],
        consulted_urls=["https://gartner.com/y"],
        mention_detected=True,
        mention_rate=1.0,
        mention_snippets=[MentionSnippet(entity="client", term="GEP", snippet="GEP is good.")],
        competitor_mentions={"coupa.com": 1},
        response_ids=["r1"],
    )


def test_snapshot_links_and_mentions_round_trip(tmp_path):
    db = TimeSeriesDB(tmp_path / "t.sqlite")
    db.upsert_prompt(_record(), "GEP")
    db.record_snapshot(PID, _snapshot(), "run1")
    stored = db.history(PID, Engine.PERPLEXITY)[0]
    assert stored == _snapshot()


def test_answer_sample_links_and_mentions_are_stored(tmp_path):
    db = TimeSeriesDB(tmp_path / "t.sqlite")
    db.upsert_prompt(_record(), "GEP")
    db.record_samples(
        PID,
        [
            AnswerSample(
                prompt_id=PID,
                engine=Engine.PERPLEXITY,
                model="sonar-pro",
                captured_at=NOW,
                web_triggered=True,
                client_cited=True,
                client_rank=1,
                cited_domains=["gep.com"],
                citation_links=[Citation(url=GEP, domain="gep.com", position=1)],
                consulted_urls=["https://gartner.com/y"],
                mention_detected=True,
                mentions=[MentionSnippet(entity="client", term="GEP", snippet="GEP is good.")],
                answer_excerpt="GEP is good.",
            )
        ],
        "run1",
    )
    conn = sqlite3.connect(tmp_path / "t.sqlite")
    try:
        row = conn.execute(
            "SELECT citation_links, consulted_urls, mention_detected, mentions FROM answer_samples"
        ).fetchone()
    finally:
        conn.close()
    assert GEP in row[0]
    assert "gartner.com" in row[1]
    assert row[2] == 1
    assert "GEP is good." in row[3]


def test_migration_adds_link_and_mention_columns_to_old_tables(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE prompts (prompt_id TEXT PRIMARY KEY, lob TEXT NOT NULL,
            brand_name TEXT NOT NULL, prompt_text TEXT NOT NULL, prompt_type TEXT NOT NULL,
            core_keyword TEXT NOT NULL, subtopic TEXT NOT NULL, search_volume INTEGER NOT NULL,
            search_intent TEXT NOT NULL, decision_stage TEXT NOT NULL, mapped_url TEXT,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
        CREATE TABLE answer_samples (id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt_id TEXT NOT NULL, run_id TEXT NOT NULL, engine TEXT NOT NULL,
            model TEXT NOT NULL, captured_at TEXT NOT NULL, response_id TEXT,
            web_triggered INTEGER NOT NULL, client_cited INTEGER NOT NULL, client_rank INTEGER,
            cited_domains TEXT NOT NULL, answer_excerpt TEXT NOT NULL);
        """
    )
    conn.commit()
    conn.close()
    TimeSeriesDB(path)
    columns = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(answer_samples)")}
    assert {"citation_links", "consulted_urls", "mention_detected", "mentions"} <= columns
    snap_columns = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(snapshots)")}
    assert {
        "citation_links",
        "client_urls",
        "mention_snippets",
        "competitor_mentions",
    } <= snap_columns
