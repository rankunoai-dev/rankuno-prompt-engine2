# ADR 0003 — SQLite for the citation time-series store

**Status**: Accepted (2026-09-16)

## Context

The tracker stores prompts, per-run citation snapshots and run headers, and
computes month-over-month velocity. Expected volume for one client LOB: 20
prompts × 4 engines × 1 run/day ≈ 2,400 snapshot rows per month. One writer.

## Decision

Use the standard-library `sqlite3` module with a three-table schema
(`prompts`, `snapshots`, `runs`) at `TRACKER_DB_PATH`. History is append-only;
prompts are upserted by a stable `prompt_id` (SHA-256 of LOB + prompt text).
Velocity is window-over-window over the stored snapshots.

## Alternatives considered

- **Postgres.** Right answer for multi-tenant or concurrent writers; wrong for a
  single analyst workstation today. Deferred until a second tenant exists.
- **CSV/Parquet files.** Rejected: no upsert, no indexed history queries.

## Consequences

- Zero infrastructure; the database is a file next to the reports.
- Schema changes need a hand-written migration (no framework). Tracked in
  `docs/KNOWN_GAPS.md`.
