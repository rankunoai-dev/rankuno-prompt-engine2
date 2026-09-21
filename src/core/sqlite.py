"""One way to open the platform's SQLite files.

Every store opens a short-lived connection per operation. This helper is the
single place that sets the pragmas those connections need to coexist under a
polling UI and a background worker:

* `journal_mode=WAL` — readers no longer block the writer or each other. The
  setting is persistent per file; issuing it on every connect is harmless.
* `busy_timeout` — 30 s on every store, replacing a mix of 30 s and the 5 s
  default that sat on the two hottest writers.
* `foreign_keys=ON` — enforced per connection, as before.

Stdlib only, so it belongs in `core`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

__all__ = ["BUSY_TIMEOUT_S", "connect"]

BUSY_TIMEOUT_S = 30.0


def connect(
    path: Path | str, *, row_factory: type[sqlite3.Row] | None = None
) -> sqlite3.Connection:
    """Open `path` with the platform pragmas applied.

    Args:
        path: Database file. Parent directories are the caller's responsibility.
        row_factory: Pass `sqlite3.Row` for name-addressable rows.
    """
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_S)
    if row_factory is not None:
        conn.row_factory = row_factory
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT_S * 1000)}")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
