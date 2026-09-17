"""Flatten the tracker SQLite store into the document `docs/prompt-atlas.html` reads.

Shape: `{meta, prompts, snapshots, runs}`.
Used by `scripts/export_dashboard.py` (writes a file) and by the control plane
(serves it live at `/reports/prompt-atlas-data.json`, so the page is never
stale). JSON columns are decoded here, once, so the page never parses SQL text.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = ["export_atlas"]

_PROMPT_COLUMNS = (
    "prompt_id, lob, brand_name, prompt_text, prompt_type, core_keyword, subtopic, "
    "search_volume, search_intent, decision_stage, mapped_url, first_seen, last_seen"
)
_SNAPSHOT_COLUMNS = (
    "prompt_id, run_id, engine, model, captured_at, samples, failed_samples, "
    "web_trigger_rate, client_cited_samples, client_citation_rate, client_cited, "
    "client_best_rank, client_mean_rank, cited_domains, competitor_citations, answer_excerpt, "
    "mention_rate, mention_snippets, competitor_mentions, citation_links, client_urls"
)
_RUN_COLUMNS = (
    "run_id, lob, brand_name, started_at, finished_at, prompts_selected, engine_calls, "
    "failed_engine_calls, estimated_cost_usd, semrush_units, report_path"
)
_JSON_COLUMNS: dict[str, str] = {
    "cited_domains": "[]",
    "competitor_citations": "{}",
    "mention_snippets": "[]",
    "competitor_mentions": "{}",
    "citation_links": "[]",
    "client_urls": "[]",
}


def _rows(conn: sqlite3.Connection, table: str, columns: str, order: str) -> list[dict[str, Any]]:
    """Read one table into dicts. Table and column names are constants, never user input."""
    query = f"SELECT {columns} FROM {table} ORDER BY {order}"  # noqa: S608 - constants only
    names = [c.strip() for c in columns.split(",")]
    return [dict(zip(names, row, strict=True)) for row in conn.execute(query).fetchall()]


def export_atlas(
    db_path: Path,
    *,
    domains: list[str] | None = None,
    competitors: list[str] | None = None,
    lob: str | None = None,
) -> dict[str, Any]:
    """Build the Prompt Atlas dataset from `db_path`.

    Args:
        db_path: Tracker SQLite file (opened read-only).
        domains: Client domains to show as "client" in share-of-voice views.
        competitors: Competitor domains to highlight.
        lob: Restrict prompts, snapshots and runs to one line of business.

    Raises:
        FileNotFoundError: No database at `db_path`.
    """
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        prompts = _rows(conn, "prompts", _PROMPT_COLUMNS, "lob, prompt_type, prompt_text")
        snapshots = _rows(conn, "snapshots", _SNAPSHOT_COLUMNS, "captured_at")
        runs = _rows(conn, "runs", _RUN_COLUMNS, "started_at")
    finally:
        conn.close()

    if lob is not None:
        prompts = [p for p in prompts if p["lob"] == lob]
        runs = [r for r in runs if r["lob"] == lob]
        keep = {p["prompt_id"] for p in prompts}
        snapshots = [s for s in snapshots if s["prompt_id"] in keep]

    for prompt in prompts:
        prompt["content_gap"] = prompt["mapped_url"] is None
        prompt["created_at"] = prompt.pop("first_seen")
    for snapshot in snapshots:
        snapshot["client_cited"] = bool(snapshot["client_cited"])
        snapshot["mention_rate"] = float(snapshot.get("mention_rate") or 0.0)
        for column, empty in _JSON_COLUMNS.items():
            snapshot[column] = json.loads(snapshot.get(column) or empty)
    for run in runs:
        run["warnings"] = list[str]()

    latest: dict[str, Any] = runs[-1] if runs else {}
    meta = {
        "source": "sqlite",
        "brand_name": latest.get("brand_name", ""),
        "lob": latest.get("lob", ""),
        "aliases": [],
        "domains": list(domains or []),
        "competitor_domains": list(competitors or []),
        "exported_at": datetime.now(UTC).isoformat(),
        "db_path": str(db_path),
    }
    return {"meta": meta, "prompts": prompts, "snapshots": snapshots, "runs": runs}
