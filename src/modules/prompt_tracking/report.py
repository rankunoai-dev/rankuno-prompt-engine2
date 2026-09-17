"""Writes the master research sheet (CSV) and the UI-ready dataset (JSON).

CSV: one row per prompt, with a five-column block per engine (cited, rate, best
rank, the client's cited URLs, the first mention snippet) so a reader can
compare engines side by side. Google organic rank is reported twice: for the
conversational prompt text and for the short seed keyword behind it.

JSON: one row per prompt × engine, flat and complete — full citation links with
positions, consulted URLs, competitor citations and mentions, mention snippets,
organic ranks — for a dashboard to render without touching the database.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from src.integrations.schemas import Engine
from src.modules.prompt_tracking.schemas import (
    ClientProfile,
    MasterPromptRecord,
    RankQueryKind,
)

__all__ = ["ENGINE_COLUMNS", "write_master_sheet", "write_ui_dataset"]

_ENGINE_LABELS = {
    Engine.GOOGLE_AI_OVERVIEW: "Google AIO",
    Engine.CHATGPT_SEARCH: "ChatGPT Search",
    Engine.PERPLEXITY: "Perplexity",
    Engine.GEMINI: "Gemini",
}
ENGINE_COLUMNS = ("Cited", "Citation Rate", "Best Rank", "Client URLs", "Mention Snippet")


def write_master_sheet(records: list[MasterPromptRecord], path: Path) -> Path:
    """Write `records` to `path` as CSV and return it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    engines = list(Engine)
    header = [
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
    for engine in engines:
        header += [f"{_ENGINE_LABELS[engine]} {col}" for col in ENGINE_COLUMNS]
    header += ["Cited Domains", "Competitor Citations", "Mapped URL", "Verdict", "Verdict Reason"]
    header += [
        "Google Organic Rank (Prompt)",
        "Ranking URL (Prompt)",
        "Google Organic Rank (Keyword)",
        "Ranking URL (Keyword)",
        "Organic Competitors",
    ]

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for record in records:
            writer.writerow(_csv_row(record, engines))
    return path


def _csv_row(record: MasterPromptRecord, engines: list[Engine]) -> list[str]:
    row: list[str] = [
        record.prompt_id,
        record.lob,
        record.subtopic,
        record.core_keyword,
        str(record.search_volume),
        record.prompt_text,
        record.prompt_type.value.replace("_", "-").title(),
        record.search_intent.value.title(),
        record.decision_stage.value.replace("_", "-").title(),
        "Yes" if record.web_triggers else "No",
    ]
    domains: list[str] = []
    competitors: list[str] = []
    for engine in engines:
        snap = record.latest(engine)
        if snap is None:
            row += ["n/a", "", "", "", ""]
            continue
        row += [
            "Yes" if snap.client_cited else "No",
            f"{snap.client_citation_rate:.2f}",
            str(snap.client_best_rank) if snap.client_best_rank else "Not cited",
            " | ".join(snap.client_urls),
            snap.mention_snippets[0].snippet if snap.mention_snippets else "",
        ]
        domains += [d for d in snap.cited_domains if d not in domains]
        competitors += [
            f"{_ENGINE_LABELS[engine]}: {dom} #{rank}"
            for dom, rank in snap.competitor_citations.items()
        ]
    row += [
        ", ".join(domains),
        "; ".join(competitors),
        record.mapped_url or f"[CONTENT GAP: Need {record.subtopic.title()} Page]",
        record.verdict.value.title(),
        record.verdict_reason,
    ]
    organic_competitors: list[str] = []
    for kind in (RankQueryKind.PROMPT, RankQueryKind.KEYWORD):
        rank = record.latest_rank(kind)
        if rank is None:
            row += ["n/a", ""]
            continue
        row += [
            str(rank.client_position) if rank.client_position else "Not ranked",
            rank.client_url or "",
        ]
        organic_competitors += [
            f"{kind.value.title()}: {dom} #{pos}" for dom, pos in rank.competitor_positions.items()
        ]
    row.append("; ".join(organic_competitors))
    return row


def write_ui_dataset(
    records: list[MasterPromptRecord], client: ClientProfile, run_id: str, path: Path
) -> Path:
    """Write the flat prompt × engine dataset as JSON and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [row for record in records for row in dataset_rows(record, client, run_id)]
    payload = {
        "run_id": run_id,
        "client": {
            "brand_name": client.brand_name,
            "aliases": client.aliases,
            "domains": client.domains,
            "competitor_domains": client.competitor_domains,
            "lob": client.lob,
        },
        "rows": rows,
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def dataset_rows(
    record: MasterPromptRecord, client: ClientProfile, run_id: str
) -> list[dict[str, Any]]:
    """One flat row per engine snapshot on `record`, plus organic ranks.

    `Any` is unavoidable for a JSON document; every value comes from a typed
    model field.
    """
    prompt_rank = record.latest_rank(RankQueryKind.PROMPT)
    keyword_rank = record.latest_rank(RankQueryKind.KEYWORD)
    rows: list[dict[str, Any]] = []
    for snap in record.citation_history:
        rows.append(
            {
                "run_id": run_id,
                "prompt_id": record.prompt_id,
                "prompt_text": record.prompt_text,
                "prompt_type": record.prompt_type.value,
                "core_keyword": record.core_keyword,
                "subtopic": record.subtopic,
                "client": client.brand_name,
                "engine": snap.engine.value,
                "model": snap.model,
                "captured_at": snap.captured_at.isoformat(),
                "samples": snap.samples,
                "failed_samples": snap.failed_samples,
                "reused": snap.reused,
                "web_triggered": snap.web_trigger_rate > 0,
                "web_trigger_rate": snap.web_trigger_rate,
                "client_cited": snap.client_cited,
                "client_citation_rate": snap.client_citation_rate,
                "client_rank": snap.client_best_rank,
                "client_mean_rank": snap.client_mean_rank,
                "client_urls": snap.client_urls,
                "mention_detected": snap.mention_detected,
                "mention_rate": snap.mention_rate,
                "mention_snippet": (
                    snap.mention_snippets[0].snippet if snap.mention_snippets else None
                ),
                "mention_snippets": [m.model_dump() for m in snap.mention_snippets],
                "citation_links": [c.model_dump() for c in snap.citation_links],
                "cited_domains": snap.cited_domains,
                "consulted_urls": snap.consulted_urls,
                "competitor_citations": snap.competitor_citations,
                "competitor_mentions": snap.competitor_mentions,
                "answer_excerpt": snap.answer_excerpt,
                "response_ids": snap.response_ids,
                "organic_rank_prompt": prompt_rank.client_position if prompt_rank else None,
                "organic_url_prompt": prompt_rank.client_url if prompt_rank else None,
                "organic_rank_keyword": keyword_rank.client_position if keyword_rank else None,
                "organic_url_keyword": keyword_rank.client_url if keyword_rank else None,
                "mapped_url": record.mapped_url,
                "content_gap": record.content_gap,
                "verdict": record.verdict.value,
            }
        )
    return rows
