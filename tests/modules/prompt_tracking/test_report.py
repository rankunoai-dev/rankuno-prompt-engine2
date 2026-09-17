"""Tests for the master research sheet CSV writer."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.integrations.schemas import Engine
from src.modules.prompt_tracking.report import write_master_sheet
from src.modules.prompt_tracking.schemas import (
    CitationSnapshot,
    DecisionStage,
    MasterPromptRecord,
    OrganicRankSnapshot,
    PromptType,
    RankQueryKind,
    SearchIntent,
    Verdict,
    prompt_id_for,
)

NOW = datetime(2026, 9, 16, tzinfo=UTC)
LABELS = ["Google AIO", "ChatGPT Search", "Perplexity", "Gemini"]
# Column offsets in a row: 10 fixed, then 5 per engine, then the trailing block.
_ENGINE_START = 10
_PER_ENGINE = 5
_DOMAINS = _ENGINE_START + _PER_ENGINE * len(LABELS)
_COMPETITORS = _DOMAINS + 1
_MAPPED = _DOMAINS + 2
_VERDICT = _DOMAINS + 3
_REASON = _DOMAINS + 4
_RANK_PROMPT = _DOMAINS + 5
_URL_PROMPT = _DOMAINS + 6
_RANK_KEYWORD = _DOMAINS + 7
_URL_KEYWORD = _DOMAINS + 8
_ORGANIC_COMPETITORS = _DOMAINS + 9


def _snapshot(
    engine: Engine,
    *,
    cited: bool,
    best_rank: int | None,
    rate: float,
    domains: list[str],
    competitors: dict[str, int] | None = None,
) -> CitationSnapshot:
    return CitationSnapshot(
        engine=engine,
        model="m",
        captured_at=NOW,
        samples=3,
        web_trigger_rate=1.0,
        client_cited_samples=int(rate * 3),
        client_citation_rate=rate,
        client_cited=cited,
        client_best_rank=best_rank,
        cited_domains=domains,
        competitor_citations=competitors or {},
    )


def _record(
    text: str,
    *,
    history: list[CitationSnapshot],
    web_triggers: bool,
    mapped_url: str | None,
    prompt_type: PromptType = PromptType.NON_BRANDED,
    verdict: Verdict = Verdict.KEEP,
) -> MasterPromptRecord:
    return MasterPromptRecord(
        prompt_id=prompt_id_for("Procurement Software", text),
        lob="Procurement Software",
        subtopic="procurement software",
        core_keyword="procurement software",
        search_volume=5400,
        prompt_text=text,
        search_intent=SearchIntent.COMMERCIAL,
        decision_stage=DecisionStage.POST_PURCHASE,
        prompt_type=prompt_type,
        web_triggers=web_triggers,
        citation_history=history,
        mapped_url=mapped_url,
        content_gap=mapped_url is None,
        verdict=verdict,
        verdict_reason="because",
    )


@pytest.fixture
def records() -> list[MasterPromptRecord]:
    cited = _record(
        "What is the best procurement software?",
        history=[
            _snapshot(
                Engine.GOOGLE_AI_OVERVIEW,
                cited=True,
                best_rank=1,
                rate=1.0,
                domains=["gep.com", "coupa.com"],
                competitors={"coupa.com": 2},
            ),
            _snapshot(
                Engine.GEMINI,
                cited=False,
                best_rank=None,
                rate=0.0,
                domains=["coupa.com", "sap.com"],
                competitors={"coupa.com": 1, "sap.com": 3},
            ),
        ],
        web_triggers=True,
        mapped_url="https://www.gep.com/software/procurement-software",
        prompt_type=PromptType.BRANDED,
    )
    gap = _record(
        "How do I renew procurement software?",
        history=[],
        web_triggers=False,
        mapped_url=None,
        verdict=Verdict.DROP,
    )
    return [cited, gap]


def _read(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))


class TestWriteMasterSheet:
    def test_creates_parent_directory_and_returns_path(self, tmp_path, records):
        target = tmp_path / "nested" / "dir" / "sheet.csv"
        assert write_master_sheet(records, target) == target
        assert target.exists()

    def test_header_has_per_engine_triples_for_all_engines(self, tmp_path, records):
        header = _read(write_master_sheet(records, tmp_path / "s.csv"))[0]
        assert header[:_ENGINE_START] == [
            "Prompt ID",
            "LOB",
            "Subtopic",
            "Core Keyword",
            "Search Volume",
            "Prompt",
            "Prompt Type",
            "Search Intent",
            "Decision Stage",
            "Web Triggers",
        ]
        for i, label in enumerate(LABELS):
            start = _ENGINE_START + _PER_ENGINE * i
            assert header[start : start + _PER_ENGINE] == [
                f"{label} Cited",
                f"{label} Citation Rate",
                f"{label} Best Rank",
                f"{label} Client URLs",
                f"{label} Mention Snippet",
            ]
        assert header[_DOMAINS:] == [
            "Cited Domains",
            "Competitor Citations",
            "Mapped URL",
            "Verdict",
            "Verdict Reason",
            "Google Organic Rank (Prompt)",
            "Ranking URL (Prompt)",
            "Google Organic Rank (Keyword)",
            "Ranking URL (Keyword)",
            "Organic Competitors",
        ]
        assert len(header) == _ORGANIC_COMPETITORS + 1

    def test_one_row_per_record_with_same_width_as_header(self, tmp_path, records):
        rows = _read(write_master_sheet(records, tmp_path / "s.csv"))
        assert len(rows) == 1 + len(records)
        assert all(len(row) == len(rows[0]) for row in rows)

    def test_cited_record_row(self, tmp_path, records):
        row = _read(write_master_sheet(records, tmp_path / "s.csv"))[1]
        assert row[:_ENGINE_START] == [
            records[0].prompt_id,
            "Procurement Software",
            "procurement software",
            "procurement software",
            "5400",
            "What is the best procurement software?",
            "Branded",
            "Commercial",
            "Post-Purchase",
            "Yes",
        ]
        # Google AIO: cited at rank 1 (no URLs/mentions on this fixture).
        assert row[_ENGINE_START : _ENGINE_START + 5] == ["Yes", "1.00", "1", "", ""]
        # ChatGPT Search and Perplexity have no snapshot.
        assert row[_ENGINE_START + 5 : _ENGINE_START + 10] == ["n/a", "", "", "", ""]
        assert row[_ENGINE_START + 10 : _ENGINE_START + 15] == ["n/a", "", "", "", ""]
        # Gemini: not cited.
        assert row[_ENGINE_START + 15 : _ENGINE_START + 20] == [
            "No",
            "0.00",
            "Not cited",
            "",
            "",
        ]
        assert row[_DOMAINS] == "gep.com, coupa.com, sap.com"
        assert (
            row[_COMPETITORS]
            == "Google AIO: coupa.com #2; Gemini: coupa.com #1; Gemini: sap.com #3"
        )
        assert row[_MAPPED] == "https://www.gep.com/software/procurement-software"
        assert row[_VERDICT] == "Keep"
        assert row[_REASON] == "because"
        # No organic history on these fixtures.
        assert row[_RANK_PROMPT : _ORGANIC_COMPETITORS + 1] == ["n/a", "", "n/a", "", ""]

    def test_organic_rank_columns(self, tmp_path, records):
        record = records[0].model_copy(
            update={
                "organic_history": [
                    OrganicRankSnapshot(
                        query=records[0].prompt_text,
                        query_kind=RankQueryKind.PROMPT,
                        captured_at=NOW,
                        samples=2,
                        client_position=None,
                        top_domains=["coupa.com"],
                        competitor_positions={"coupa.com": 1},
                    ),
                    OrganicRankSnapshot(
                        query="procurement software",
                        query_kind=RankQueryKind.KEYWORD,
                        captured_at=NOW,
                        samples=1,
                        client_position=3,
                        client_url="https://www.gep.com/software/procurement-software",
                        top_domains=["coupa.com", "sap.com", "gep.com"],
                        competitor_positions={"coupa.com": 1, "sap.com": 2},
                    ),
                ]
            }
        )
        row = _read(write_master_sheet([record], tmp_path / "s.csv"))[1]
        assert row[_RANK_PROMPT] == "Not ranked"
        assert row[_URL_PROMPT] == ""
        assert row[_RANK_KEYWORD] == "3"
        assert row[_URL_KEYWORD] == "https://www.gep.com/software/procurement-software"
        assert row[_ORGANIC_COMPETITORS] == (
            "Prompt: coupa.com #1; Keyword: coupa.com #1; Keyword: sap.com #2"
        )

    def test_gap_record_row(self, tmp_path, records):
        row = _read(write_master_sheet(records, tmp_path / "s.csv"))[2]
        assert row[6] == "Non-Branded"
        assert row[9] == "No"
        for i in range(len(LABELS)):
            start = _ENGINE_START + _PER_ENGINE * i
            assert row[start : start + _PER_ENGINE] == ["n/a", "", "", "", ""]
        assert row[_DOMAINS] == ""
        assert row[_COMPETITORS] == ""
        assert row[_MAPPED] == "[CONTENT GAP: Need Procurement Software Page]"
        assert row[_VERDICT] == "Drop"

    def test_empty_records_writes_header_only(self, tmp_path):
        rows = _read(write_master_sheet([], tmp_path / "empty.csv"))
        assert len(rows) == 1
