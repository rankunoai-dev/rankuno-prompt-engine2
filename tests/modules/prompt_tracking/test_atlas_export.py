"""Atlas export: decoded JSON columns, mention rate, LOB filter, missing database."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.integrations.schemas import Engine
from src.modules.prompt_tracking.atlas_export import export_atlas
from src.modules.prompt_tracking.schemas import (
    CitationSnapshot,
    DecisionStage,
    MasterPromptRecord,
    MentionSnippet,
    PromptType,
    SearchIntent,
    TrackerRunSummary,
    Verdict,
    prompt_id_for,
)
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB

NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)


def _seed(db: TimeSeriesDB, lob: str, text: str, mention_rate: float) -> str:
    pid = prompt_id_for(lob, text)
    db.upsert_prompt(
        MasterPromptRecord(
            prompt_id=pid,
            lob=lob,
            subtopic="S",
            core_keyword="k",
            search_volume=10,
            prompt_text=text,
            search_intent=SearchIntent.INFORMATIONAL,
            decision_stage=DecisionStage.AWARENESS,
            prompt_type=PromptType.NON_BRANDED,
            web_triggers=True,
            verdict=Verdict.KEEP,
            verdict_reason="x",
        ),
        "GEP",
    )
    db.record_snapshot(
        pid,
        CitationSnapshot(
            engine=Engine.PERPLEXITY,
            model="sonar-pro",
            captured_at=NOW,
            samples=2,
            web_trigger_rate=1.0,
            client_cited_samples=1,
            client_citation_rate=0.5,
            client_cited=True,
            client_best_rank=2,
            cited_domains=["gep.com", "coupa.com"],
            competitor_citations={"coupa.com": 1},
            mention_rate=mention_rate,
            mention_snippets=[
                MentionSnippet(entity="GEP", term="GEP", snippet="GEP SMART is a strong option.")
            ]
            if mention_rate
            else [],
        ),
        f"run-{lob}",
    )
    db.record_run(
        TrackerRunSummary(
            run_id=f"run-{lob}".ljust(8, "x"),
            lob=lob,
            brand_name="GEP",
            started_at=NOW,
            finished_at=NOW,
            candidates_generated=1,
            candidates_kept=1,
            prompts_selected=1,
            engine_calls=2,
            failed_engine_calls=0,
            estimated_cost_usd=0.04,
            semrush_units=0,
        )
    )
    return pid


def test_export_decodes_columns_and_carries_mentions(tmp_path):
    db = TimeSeriesDB(tmp_path / "t.sqlite")
    pid = _seed(db, "Procurement", "What is procurement software?", 0.5)
    doc = export_atlas(db.path, domains=["gep.com"], competitors=["coupa.com"])
    assert doc["meta"]["source"] == "sqlite" and doc["meta"]["brand_name"] == "GEP"
    assert doc["meta"]["domains"] == ["gep.com"] and doc["meta"]["competitor_domains"] == [
        "coupa.com"
    ]
    (prompt,) = doc["prompts"]
    assert prompt["prompt_id"] == pid and prompt["content_gap"] is True
    assert "created_at" in prompt and "first_seen" not in prompt
    (snap,) = doc["snapshots"]
    assert snap["client_cited"] is True
    assert snap["cited_domains"] == ["gep.com", "coupa.com"]
    assert snap["competitor_citations"] == {"coupa.com": 1}
    assert snap["mention_rate"] == 0.5
    assert snap["mention_snippets"][0]["snippet"] == "GEP SMART is a strong option."
    assert isinstance(snap["citation_links"], list) and isinstance(snap["client_urls"], list)
    (run,) = doc["runs"]
    assert run["warnings"] == [] and run["engine_calls"] == 2


def test_export_filters_by_lob(tmp_path):
    db = TimeSeriesDB(tmp_path / "t.sqlite")
    _seed(db, "Alpha", "Prompt for alpha?", 0.0)
    _seed(db, "Beta", "Prompt for beta?", 1.0)
    full = export_atlas(db.path)
    assert len(full["prompts"]) == 2 and len(full["snapshots"]) == 2 and len(full["runs"]) == 2
    beta = export_atlas(db.path, lob="Beta")
    assert [p["lob"] for p in beta["prompts"]] == ["Beta"]
    assert len(beta["snapshots"]) == 1 and beta["snapshots"][0]["mention_rate"] == 1.0
    assert [r["lob"] for r in beta["runs"]] == ["Beta"]
    assert beta["meta"]["lob"] == "Beta"


def test_missing_database_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        export_atlas(Path(tmp_path / "nope.sqlite"))


def test_script_wrapper_writes_file_and_reports_missing_db(tmp_path, capsys, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "export_dashboard", Path("scripts") / "export_dashboard.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    db = TimeSeriesDB(tmp_path / "t.sqlite")
    _seed(db, "Procurement", "What is procurement software?", 0.25)
    out = tmp_path / "out" / "atlas.json"
    assert module.main(["--db", str(db.path), "--out", str(out), "--domain", "gep.com"]) == 0
    assert out.exists() and '"mention_rate": 0.25' in out.read_text(encoding="utf-8")
    assert "1 prompts, 1 snapshots, 1 runs" in capsys.readouterr().out
    assert module.main(["--db", str(tmp_path / "missing.sqlite"), "--out", str(out)]) == 1
    assert "No database" in capsys.readouterr().err
