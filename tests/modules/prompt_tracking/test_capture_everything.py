"""Cycle 0011: full text, fan-out queries, claims, snippets and SERP extras survive the store."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from src.integrations.schemas import (
    Citation,
    CitationClaim,
    Engine,
    EngineAnswer,
    OrganicResult,
    SerpSnapshot,
    SourceSnippet,
)
from src.modules.prompt_tracking.assembly import answer_samples
from src.modules.prompt_tracking.organic import build_organic_snapshot
from src.modules.prompt_tracking.schemas import (
    ClientProfile,
    DecisionStage,
    IntentAction,
    IntentDecision,
    MasterPromptRecord,
    PromptCandidate,
    PromptType,
    RankQueryKind,
    SearchIntent,
    Verdict,
    prompt_id_for,
)
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB

NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)
LOB = "Procurement Software"
TEXT = "What is procurement software?"
PID = prompt_id_for(LOB, TEXT)
LONG_ANSWER = "GEP SMART is a suite. " * 40  # well past the 300-char excerpt


def _client() -> ClientProfile:
    return ClientProfile(
        brand_name="GEP",
        aliases=["GEP SMART"],
        domains=["gep.com"],
        competitor_domains=["coupa.com"],
        lob=LOB,
        seed_keywords=["procurement software"],
    )


def _candidate() -> PromptCandidate:
    return PromptCandidate(
        prompt_text=TEXT,
        prompt_type=PromptType.NON_BRANDED,
        decision_stage=DecisionStage.AWARENESS,
        search_intent=SearchIntent.INFORMATIONAL,
        subtopic="Procurement",
        core_keyword="procurement software",
        search_volume=100,
        intent=IntentDecision(
            prompt_text=TEXT,
            action=IntentAction.KEEP,
            score=0.8,
            entity_type="Software product / solution",
            reason="test",
        ),
    )


def _answer() -> EngineAnswer:
    return EngineAnswer(
        engine=Engine.PERPLEXITY,
        model="perplexity/sonar",
        prompt=TEXT,
        answer_text=LONG_ANSWER,
        web_triggered=True,
        citations=[Citation(url="https://www.gep.com/x", domain="gep.com", position=1)],
        consulted_urls=["https://www.gep.com/read-only"],
        search_queries=["gep smart review", "site:gep.com s2p"],
        citation_claims=[
            CitationClaim(
                url="https://www.gep.com/x", sentence="GEP SMART is a suite.", start=0, end=21
            )
        ],
        source_snippets=[
            SourceSnippet(
                url="https://www.gep.com/x", snippet="Unified S2P.", title="GEP", date="2026-01-02"
            )
        ],
        captured_at=NOW,
        response_id="resp-1",
    )


def _record() -> MasterPromptRecord:
    return MasterPromptRecord(
        prompt_id=PID,
        lob=LOB,
        subtopic="Procurement",
        core_keyword="procurement software",
        search_volume=100,
        prompt_text=TEXT,
        search_intent=SearchIntent.INFORMATIONAL,
        decision_stage=DecisionStage.AWARENESS,
        prompt_type=PromptType.NON_BRANDED,
        web_triggers=True,
        verdict=Verdict.KEEP,
        verdict_reason="x",
    )


def test_answer_samples_carry_full_text_queries_claims_and_snippets():
    (sample,) = answer_samples([_answer()], _client(), _candidate())
    assert sample.answer_text == LONG_ANSWER and len(sample.answer_excerpt) == 300
    assert sample.search_queries == ["gep smart review", "site:gep.com s2p"]
    assert sample.citation_claims[0].sentence == "GEP SMART is a suite."
    assert sample.source_snippets[0].date == "2026-01-02"
    assert sample.mention_detected is True


def test_samples_round_trip_and_readers(tmp_path):
    db = TimeSeriesDB(tmp_path / "t.sqlite")
    db.upsert_prompt(_record(), "GEP")
    (sample,) = answer_samples([_answer()], _client(), _candidate())
    db.record_samples(PID, [sample], "run-a")
    db.record_samples(PID, [sample.model_copy(update={"engine": Engine.GEMINI})], "run-b")

    rows = db.samples_for(PID)
    assert len(rows) == 2
    stored = next(r for r in rows if r.engine is Engine.PERPLEXITY)
    assert stored.answer_text == LONG_ANSWER
    assert stored.search_queries == ["gep smart review", "site:gep.com s2p"]
    assert stored.citation_claims == sample.citation_claims
    assert stored.source_snippets == sample.source_snippets
    assert stored.citation_links[0].domain == "gep.com" and stored.consulted_urls
    assert stored.mentions and stored.mentions[0].entity == "client"
    assert [r.engine for r in db.samples_for(PID, engine=Engine.GEMINI)] == [Engine.GEMINI]
    assert [r.engine for r in db.samples_for(PID, run_ids=["run-a"])] == [Engine.PERPLEXITY]
    assert db.samples_for(PID, run_ids=[]) == []
    assert db.sample_run_ids(PID) == ["run-a", "run-b"] or set(db.sample_run_ids(PID)) == {
        "run-a",
        "run-b",
    }
    meta = db.prompt_records([PID, "missing"])
    assert meta[PID]["search_volume"] == 100 and meta[PID]["subtopic"] == "Procurement"
    assert meta[PID]["mapped_url"] is None and "missing" not in meta
    assert db.prompt_records([]) == {}


def test_organic_snapshot_keeps_results_paa_and_related(tmp_path):
    serp = SerpSnapshot(
        query=TEXT,
        ai_overview_present=False,
        paa_questions=["What does procurement software do?", "Is it expensive?"],
        related_searches=["procurement software list"],
        organic_results=[
            OrganicResult(
                position=2, url="https://www.gep.com/p", domain="gep.com", title="GEP", snippet="S"
            ),
            OrganicResult(
                position=1, url="https://www.coupa.com/", domain="coupa.com", title="Coupa"
            ),
        ],
    )
    snapshot = build_organic_snapshot(RankQueryKind.PROMPT, TEXT, [serp, serp], _client())
    assert snapshot is not None
    assert [r.position for r in snapshot.organic_results] == [1, 2]
    assert snapshot.organic_results[1].snippet == "S"
    assert snapshot.paa_questions == ["What does procurement software do?", "Is it expensive?"]
    assert snapshot.related_searches == ["procurement software list"]
    assert snapshot.client_position == 2

    db = TimeSeriesDB(tmp_path / "t.sqlite")
    db.upsert_prompt(_record(), "GEP")
    db.record_organic(PID, snapshot, "run-a")
    (stored,) = db.organic_history(PID, RankQueryKind.PROMPT)
    assert stored.organic_results == snapshot.organic_results
    assert stored.paa_questions == snapshot.paa_questions
    assert stored.related_searches == snapshot.related_searches


def test_migration_adds_capture_columns_to_old_tables(tmp_path):
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
        CREATE TABLE organic_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt_id TEXT NOT NULL, run_id TEXT NOT NULL, query TEXT NOT NULL,
            query_kind TEXT NOT NULL, device TEXT NOT NULL, captured_at TEXT NOT NULL,
            samples INTEGER NOT NULL, client_position INTEGER, client_url TEXT,
            top_domains TEXT NOT NULL, competitor_positions TEXT NOT NULL);
        INSERT INTO organic_snapshots (prompt_id, run_id, query, query_kind, device, captured_at,
            samples, client_position, client_url, top_domains, competitor_positions)
        VALUES ('p', 'r', 'q', 'PROMPT', 'desktop', '2026-09-17T00:00:00+00:00', 1, 3, 'u',
            '["gep.com"]', '{}');
        """
    )
    conn.commit()
    conn.close()
    db = TimeSeriesDB(path)
    columns = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(answer_samples)")}
    assert {"answer_text", "search_queries", "citation_claims", "source_snippets"} <= columns
    organic_columns = {
        r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(organic_snapshots)")
    }
    assert {"organic_results", "paa_questions", "related_searches"} <= organic_columns
    (old,) = db.organic_history("p", RankQueryKind.PROMPT)
    assert old.client_position == 3 and old.organic_results == [] and old.paa_questions == []
