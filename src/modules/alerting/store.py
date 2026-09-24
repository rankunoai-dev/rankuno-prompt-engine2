"""Destinations and fired alerts, in the tracker database (ADR 0024).

The destination table is the reason this is a table and not a field on the
project payload: `GET /api/projects/{id}` is readable by anyone who can reach
the deployment (ADR 0019 — everyone reads, only the holder writes), and a
Slack webhook URL is a bearer credential while a recipient list is personal
data. Neither may travel in that payload. They live here, behind the owner
check, and leave the process only as masked hints.

The event table is both the dedupe ledger and the audit trail: a row is
written *before* delivery is attempted, so a crash between send and record can
duplicate a message but never lose the fact that it was sent.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from src.core.logger import get_logger
from src.core.sqlite import connect
from src.integrations.email_send import valid_address
from src.integrations.schemas import Engine
from src.integrations.slack import describe_webhook, valid_webhook
from src.modules.alerting.schemas import (
    DEFAULT_RULES,
    AlertChannel,
    AlertDestination,
    AlertDestinationUpdate,
    AlertDestinationView,
    AlertEvent,
    AlertRecord,
    AlertRule,
    AlertSeverity,
)

__all__ = ["AlertStore", "mask_address"]

_logger = get_logger("modules.alerting.store")

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS alert_destinations (
    project_id    TEXT PRIMARY KEY,
    enabled       INTEGER NOT NULL DEFAULT 0,
    slack_webhook TEXT,
    email_to      TEXT NOT NULL DEFAULT '[]',
    rules         TEXT NOT NULL DEFAULT '[]',
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alert_events (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL,
    rule              TEXT NOT NULL,
    severity          TEXT NOT NULL,
    dedupe_key        TEXT NOT NULL,
    title             TEXT NOT NULL,
    detail            TEXT NOT NULL,
    engine            TEXT,
    url               TEXT,
    fired_at          TEXT NOT NULL,
    channels          TEXT NOT NULL DEFAULT '[]',
    delivered         INTEGER NOT NULL DEFAULT 0,
    suppressed_reason TEXT,
    error             TEXT
);
CREATE INDEX IF NOT EXISTS ix_alert_events_project ON alert_events (project_id, fired_at DESC);
CREATE INDEX IF NOT EXISTS ix_alert_events_key ON alert_events (project_id, dedupe_key, fired_at);
"""

_EVENT_COLUMNS: Final = (
    "id, project_id, rule, severity, dedupe_key, title, detail, engine, url, fired_at, "
    "channels, delivered, suppressed_reason, error"
)


def mask_address(address: str) -> str:
    """`priya@client.com` becomes `p***@client.com`."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def _event_row(row: tuple[object, ...]) -> AlertRecord:
    """Rebuild a fired alert from its row."""
    return AlertRecord(
        id=str(row[0]),
        project_id=str(row[1]),
        rule=AlertRule(str(row[2])),
        severity=AlertSeverity(str(row[3])),
        dedupe_key=str(row[4]),
        title=str(row[5]),
        detail=str(row[6]),
        engine=Engine(str(row[7])) if row[7] else None,
        url=str(row[8]) if row[8] else None,
        fired_at=datetime.fromisoformat(str(row[9])),
        channels=[AlertChannel(value) for value in json.loads(str(row[10]))],
        delivered=bool(row[11]),
        suppressed_reason=str(row[12]) if row[12] else None,
        error=str(row[13]) if row[13] else None,
    )


class AlertStore:
    """Per-project destinations and the alert history."""

    def __init__(self, db_path: Path) -> None:
        """Create the schema."""
        self._path = db_path
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open the tracker database, commit, and always close.

        `connect()` hands back a raw connection: `with` on it commits, but it
        does not close, so a `with connect(...)` here would leak a handle per
        call and leave the file's WAL behind. Same shape as every other store.
        """
        conn = connect(self._path)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # -- destinations ------------------------------------------------------

    def destination(self, project_id: str) -> AlertDestination:
        """The project's destination; an empty, disabled one when unset."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT project_id, enabled, slack_webhook, email_to, rules, updated_at "
                "FROM alert_destinations WHERE project_id=?",
                (project_id,),
            ).fetchone()
        if row is None:
            return AlertDestination(project_id=project_id, rules=list(DEFAULT_RULES))
        return AlertDestination(
            project_id=str(row[0]),
            enabled=bool(row[1]),
            slack_webhook=str(row[2]) if row[2] else None,
            email_to=list(json.loads(str(row[3]))),
            rules=[AlertRule(value) for value in json.loads(str(row[4]))],
            updated_at=datetime.fromisoformat(str(row[5])),
        )

    def view(self, project_id: str) -> AlertDestinationView:
        """What a reader may see: configured or not, and masked hints."""
        destination = self.destination(project_id)
        return AlertDestinationView(
            project_id=project_id,
            enabled=destination.enabled,
            slack_configured=bool(destination.slack_webhook),
            slack_hint=(
                describe_webhook(destination.slack_webhook) if destination.slack_webhook else None
            ),
            email_count=len(destination.email_to),
            email_hints=[mask_address(address) for address in destination.email_to],
            rules=list(destination.rules),
            updated_at=destination.updated_at,
        )

    def save_destination(
        self, project_id: str, update: AlertDestinationUpdate
    ) -> AlertDestinationView:
        """Apply an owner's change and return the safe view.

        An empty `slack_webhook` clears it; a malformed one is refused, because
        the alternative is storing an arbitrary URL this application will later
        POST to on a schedule.
        """
        current = self.destination(project_id)
        webhook = current.slack_webhook
        if update.slack_webhook is not None:
            candidate = update.slack_webhook.strip()
            if not candidate:
                webhook = None
            elif valid_webhook(candidate):
                webhook = candidate
            else:
                msg = "Slack destination must be an https://hooks.slack.com/services/... URL."
                raise ValueError(msg)
        recipients = current.email_to
        if update.email_to is not None:
            bad = [address for address in update.email_to if not valid_address(address)]
            if bad:
                msg = f"{len(bad)} recipient(s) are not valid email addresses."
                raise ValueError(msg)
            recipients = list(update.email_to)
        rules = current.rules if update.rules is None else list(dict.fromkeys(update.rules))
        enabled = current.enabled if update.enabled is None else update.enabled
        if enabled and not webhook and not recipients:
            msg = "Add a Slack webhook or at least one email recipient before enabling alerts."
            raise ValueError(msg)
        now = datetime.now(UTC)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO alert_destinations (project_id, enabled, slack_webhook, email_to, "
                "rules, updated_at) VALUES (?,?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET "
                "enabled=excluded.enabled, slack_webhook=excluded.slack_webhook, "
                "email_to=excluded.email_to, rules=excluded.rules, updated_at=excluded.updated_at",
                (
                    project_id,
                    int(enabled),
                    webhook,
                    json.dumps(recipients),
                    json.dumps([rule.value for rule in rules]),
                    now.isoformat(),
                ),
            )
        _logger.info(
            "alert_destination_saved",
            extra={
                "project_id": project_id,
                "enabled": enabled,
                "slack": bool(webhook),
                "recipients": len(recipients),
                "rules": [rule.value for rule in rules],
            },
        )
        return self.view(project_id)

    def delete_project(self, project_id: str) -> None:
        """Forget a project's destination and its alert history."""
        with self._connect() as conn:
            conn.execute("DELETE FROM alert_destinations WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM alert_events WHERE project_id=?", (project_id,))

    # -- events ------------------------------------------------------------

    def record(
        self,
        project_id: str,
        event: AlertEvent,
        *,
        channels: list[AlertChannel],
        suppressed_reason: str | None,
        now: datetime | None = None,
    ) -> AlertRecord:
        """Write the event before any send is attempted."""
        record = AlertRecord(
            id=uuid.uuid4().hex[:16],
            project_id=project_id,
            rule=event.rule,
            severity=event.severity,
            dedupe_key=event.dedupe_key,
            title=event.title,
            detail=event.detail,
            engine=event.engine,
            url=event.url,
            fired_at=now or datetime.now(UTC),
            channels=channels,
            delivered=False,
            suppressed_reason=suppressed_reason,
        )
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO alert_events ({_EVENT_COLUMNS}) "  # noqa: S608 - constant columns
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.id,
                    record.project_id,
                    record.rule.value,
                    record.severity.value,
                    record.dedupe_key,
                    record.title,
                    record.detail,
                    record.engine.value if record.engine else None,
                    record.url,
                    record.fired_at.isoformat(),
                    json.dumps([channel.value for channel in record.channels]),
                    0,
                    record.suppressed_reason,
                    None,
                ),
            )
        return record

    def mark(self, record: AlertRecord) -> AlertRecord:
        """Write delivery outcome back."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE alert_events SET delivered=?, error=?, channels=? WHERE id=?",
                (
                    int(record.delivered),
                    record.error,
                    json.dumps([channel.value for channel in record.channels]),
                    record.id,
                ),
            )
        return record

    def fired_since(self, project_id: str, dedupe_key: str, since: datetime) -> bool:
        """True when this exact alert already went out inside the cooldown."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM alert_events WHERE project_id=? AND dedupe_key=? AND delivered=1 "
                "AND fired_at >= ? LIMIT 1",
                (project_id, dedupe_key, since.isoformat()),
            ).fetchone()
        return row is not None

    def delivered_since(self, project_id: str, since: datetime) -> int:
        """How many alerts this project has actually sent since `since`."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM alert_events WHERE project_id=? AND delivered=1 "
                "AND fired_at >= ?",
                (project_id, since.isoformat()),
            ).fetchone()
        return int(str(row[0])) if row else 0

    def list_for(self, project_id: str, limit: int = 50) -> list[AlertRecord]:
        """A project's alert history, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_EVENT_COLUMNS} FROM alert_events WHERE project_id=? "  # noqa: S608
                "ORDER BY fired_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        return [_event_row(row) for row in rows]
