"""Deciding whether to send, and sending it (ADR 0024).

Three gates stand between a true event and somebody's phone buzzing:

1. **The rule is on.** Only `CITATION_DROP` is on for a new project.
2. **The cooldown has expired.** The same rule, platform and subject stays
   quiet for `ALERT_COOLDOWN_HOURS` after a delivered alert. A metric sitting
   just past the threshold must not re-announce itself every crawl.
3. **The daily ceiling has room.** `ALERTS_MAX_PER_PROJECT_PER_DAY` caps how
   many messages one project can produce in 24 hours, whatever happens
   upstream.

Suppressed events are still written to the history with the reason, so
"why didn't I hear about this?" has an answer.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from src.core.config import Settings, get_settings
from src.core.errors import IntegrationError
from src.core.logger import get_logger
from src.integrations.email_send import EmailClient
from src.integrations.slack import SlackWebhookClient
from src.modules.alerting.schemas import (
    AlertChannel,
    AlertDestination,
    AlertEvent,
    AlertRecord,
    AlertSeverity,
)
from src.modules.alerting.store import AlertStore

__all__ = ["AlertDispatcher"]

_logger = get_logger("modules.alerting.dispatch")

_EMOJI: Final[dict[AlertSeverity, str]] = {
    AlertSeverity.CRITICAL: "🔴",
    AlertSeverity.WARNING: "🟠",
    AlertSeverity.INFO: "🔵",
}


def slack_blocks(project_name: str, event: AlertEvent) -> list[dict[str, Any]]:
    """The message as Slack blocks: headline, detail, then the evidence link."""
    mark = _EMOJI[event.severity]
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{mark} *{event.title}*\n_{project_name}_"},
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": event.detail[:2800]}},
    ]
    if event.url:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"Source: <{event.url}>"}],
            }
        )
    return blocks


def email_body(project_name: str, event: AlertEvent) -> str:
    """The same message as plain text."""
    lines = [event.title, "", event.detail, "", f"Project: {project_name}"]
    if event.url:
        lines += ["", f"Source: {event.url}"]
    return "\n".join(lines)


class AlertDispatcher:
    """Applies the gates, then delivers."""

    def __init__(
        self,
        store: AlertStore,
        *,
        settings: Settings | None = None,
        slack: SlackWebhookClient | None = None,
        mailer: EmailClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Clients are injected in tests; built on demand in production."""
        self._store = store
        self._settings = settings or get_settings()
        self._slack = slack
        self._mailer = mailer
        self._clock = clock or (lambda: datetime.now(UTC))

    def dispatch(
        self, project_id: str, project_name: str, events: list[AlertEvent]
    ) -> list[AlertRecord]:
        """Record every event and send the ones that pass the gates."""
        if not events:
            return []
        destination = self._store.destination(project_id)
        now = self._clock()
        ceiling = self._settings.alerts_max_per_project_per_day
        sent_today = self._store.delivered_since(project_id, now - timedelta(days=1))
        cooldown_start = now - timedelta(hours=self._settings.alert_cooldown_hours)
        records: list[AlertRecord] = []
        for event in events:
            reason = self._suppressed(
                destination, event, project_id, cooldown_start, sent_today, ceiling
            )
            channels = [] if reason else self._channels(destination)
            if not reason and not channels:
                reason = "no_destination"
            record = self._store.record(
                project_id, event, channels=channels, suppressed_reason=reason, now=now
            )
            if reason:
                records.append(record)
                continue
            self._deliver(record, destination, project_name, event)
            if record.delivered:
                sent_today += 1
            records.append(self._store.mark(record))
        return records

    def _suppressed(
        self,
        destination: AlertDestination,
        event: AlertEvent,
        project_id: str,
        cooldown_start: datetime,
        sent_today: int,
        ceiling: int,
    ) -> str | None:
        """Why this event will not be sent, or None when it will."""
        if not destination.enabled:
            return "alerts_off"
        if event.rule not in destination.rules:
            return "rule_off"
        if ceiling <= 0:
            return "sending_disabled"
        if sent_today >= ceiling:
            return "daily_limit"
        if self._store.fired_since(project_id, event.dedupe_key, cooldown_start):
            return "cooldown"
        return None

    def _channels(self, destination: AlertDestination) -> list[AlertChannel]:
        """Which channels this destination can actually reach."""
        channels: list[AlertChannel] = []
        if destination.slack_webhook:
            channels.append(AlertChannel.SLACK)
        if destination.email_to and (self._mailer or EmailClient(self._settings)).configured:
            channels.append(AlertChannel.EMAIL)
        return channels

    def _deliver(
        self,
        record: AlertRecord,
        destination: AlertDestination,
        project_name: str,
        event: AlertEvent,
    ) -> None:
        """Send on every available channel; partial success still counts."""
        errors: list[str] = []
        delivered: list[AlertChannel] = []
        if AlertChannel.SLACK in record.channels and destination.slack_webhook:
            client = self._slack or SlackWebhookClient(self._settings)
            try:
                client.post(
                    destination.slack_webhook,
                    text=f"{event.title} — {project_name}",
                    blocks=slack_blocks(project_name, event),
                )
                delivered.append(AlertChannel.SLACK)
            except (IntegrationError, OSError) as error:
                errors.append(f"slack: {error}")
        if AlertChannel.EMAIL in record.channels and destination.email_to:
            mailer = self._mailer or EmailClient(self._settings)
            try:
                if mailer.send(
                    to=destination.email_to,
                    subject=f"[{project_name}] {event.title}",
                    body=email_body(project_name, event),
                ):
                    delivered.append(AlertChannel.EMAIL)
            except (IntegrationError, ValueError) as error:
                errors.append(f"email: {error}")
        record.channels = delivered
        record.delivered = bool(delivered)
        record.error = "; ".join(errors)[:500] or None
        if errors:
            _logger.warning(
                "alert_delivery_failed",
                extra={"rule": record.rule.value, "errors": len(errors)},
            )
        else:
            _logger.info(
                "alert_delivered",
                extra={
                    "rule": record.rule.value,
                    "severity": record.severity.value,
                    "channels": [channel.value for channel in delivered],
                },
            )
