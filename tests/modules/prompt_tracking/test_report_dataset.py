"""Tests for the UI-ready JSON dataset and link/mention columns in the CSV."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime

from src.integrations.schemas import Citation, Engine
from src.modules.prompt_tracking.report import dataset_rows, write_master_sheet, write_ui_dataset
from src.modules.prompt_tracking.schemas import (
    CitationSnapshot,
    ClientProfile,
    DecisionStage,
    MasterPromptRecord,
    MentionSnippet,
    OrganicRankSnapshot,
    PromptType,
    RankQueryKind,
    SearchIntent,
    Verdict,
    prompt_id_for,
)

NOW = datetime(2026, 9, 16, tzinfo=UTC)
GEP = "https://www.gep.com/software/procurement-software"
COUPA = "https://www.coupa.com/products/procurement"


def _client() -> ClientProfile:
    return ClientProfile(
        brand_name="GEP",
        aliases=["GEP SMART"],
        domains=["gep.com"],
        competitor_domains=["coupa.com"],
        lob="Procurement Software",
        seed_keywords=["procurement software"],
    )


def _snapshot() -> CitationSnapshot:
    return CitationSnapshot(
        engine=Engine.CHATGPT_SEARCH,
        model="gpt-4o-mini",
        captured_at=NOW,
        samples=2,
        web_trigger_rate=1.0,
        client_cited_samples=2,
        client_citation_rate=1.0,
        client_cited=True,
        client_best_rank=2,
        client_mean_rank=2.0,
        cited_domains=["coupa.com", "gep.com"],
        competitor_citations={"coupa.com": 1},
        citation_links=[
            Citation(url=COUPA, domain="coupa.com", position=1, title="Coupa"),
            Citation(url=GEP, domain="gep.com", position=2),
        ],
        client_urls=[GEP],
        consulted_urls=["https://www.gartner.com/x"],
        mention_detected=True,
        mention_rate=0.5,
        mention_snippets=[
            MentionSnippet(entity="client", term="GEP SMART", snippet="GEP SMART is strong.")
        ],
        competitor_mentions={"coupa.com": 2},
        response_ids=["r1", "r2"],
    )


def _record() -> MasterPromptRecord:
    text = "What is the best procurement software for enterprises?"
    return MasterPromptRecord(
        prompt_id=prompt_id_for("Procurement Software", text),
        lob="Procurement Software",
        subtopic="Procurement Software",
        core_keyword="procurement software",
        search_volume=5400,
        prompt_text=text,
        search_intent=SearchIntent.COMMERCIAL,
        decision_stage=DecisionStage.CONSIDERATION,
        prompt_type=PromptType.NON_BRANDED,
        web_triggers=True,
        citation_history=[_snapshot()],
        organic_history=[
            OrganicRankSnapshot(
                query=text,
                query_kind=RankQueryKind.PROMPT,
                captured_at=NOW,
                samples=1,
                client_position=4,
                client_url=GEP,
            )
        ],
        mapped_url=GEP,
        verdict=Verdict.KEEP,
        verdict_reason="ok",
    )


def test_dataset_rows_match_the_ui_contract():
    rows = dataset_rows(_record(), _client(), "run1")
    assert len(rows) == 1
    row = rows[0]
    assert row["prompt_text"] == "What is the best procurement software for enterprises?"
    assert row["client"] == "GEP"
    assert row["engine"] == "CHATGPT_SEARCH"
    assert row["web_triggered"] is True
    assert row["client_cited"] is True
    assert row["client_rank"] == 2
    assert row["mention_detected"] is True
    assert row["mention_snippet"] == "GEP SMART is strong."
    assert row["citation_links"] == [
        {"position": 1, "url": COUPA, "domain": "coupa.com", "title": "Coupa", "resolved": True},
        {"position": 2, "url": GEP, "domain": "gep.com", "title": None, "resolved": True},
    ]
    assert row["consulted_urls"] == ["https://www.gartner.com/x"]
    assert row["competitor_citations"] == {"coupa.com": 1}
    assert row["competitor_mentions"] == {"coupa.com": 2}
    assert row["organic_rank_prompt"] == 4
    assert row["organic_rank_keyword"] is None
    assert row["run_id"] == "run1"


def test_write_ui_dataset_writes_json_with_client_header(tmp_path):
    path = write_ui_dataset([_record()], _client(), "run1", tmp_path / "out" / "d.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run1"
    assert payload["client"]["brand_name"] == "GEP"
    assert payload["client"]["aliases"] == ["GEP SMART"]
    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["client_urls"] == [GEP]


def test_csv_engine_block_includes_client_urls_and_snippet(tmp_path):
    path = write_master_sheet([_record()], tmp_path / "s.csv")
    with path.open(newline="", encoding="utf-8") as handle:
        header, row = list(csv.reader(handle))[:2]
    start = header.index("ChatGPT Search Cited")
    assert row[start : start + 5] == ["Yes", "1.00", "2", GEP, "GEP SMART is strong."]


def test_record_without_snapshots_yields_no_rows():
    record = _record().model_copy(update={"citation_history": []})
    assert dataset_rows(record, _client(), "r") == []
