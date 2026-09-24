"""Contracts for alert rules, destinations and fired events (ADR 0024).

The destination carries credentials and personal data — a Slack webhook URL is
a bearer token, and a recipient list is personal data — so it never crosses the
API boundary as written. `AlertDestination` is the stored shape;
`AlertDestinationView` is the only shape a reader ever sees.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from src.core.schemas import StrictModel
from src.integrations.schemas import Engine

__all__ = [
    "DEFAULT_RULES",
    "AlertChannel",
    "AlertDestination",
    "AlertDestinationUpdate",
    "AlertDestinationView",
    "AlertEvent",
    "AlertRecord",
    "AlertRule",
    "AlertSeverity",
]


class AlertRule(StrEnum):
    """What an alert can be about."""

    CITATION_DROP = "citation_drop"
    """The engine's citation rate fell and the two 95% intervals do not overlap."""

    LOST_PROMPT = "lost_prompt"
    """A prompt the analyst starred stopped being cited."""

    COMPETITOR_SURGE = "competitor_surge"
    """A rival crossed the share threshold on several prompts at once."""

    NEGATIVE_CLAIM = "negative_claim"
    """An engine said something negative about the brand, with a source."""

    ENGINE_SILENT = "engine_silent"
    """A platform that used to answer now returns nothing for this project."""

    SPEND = "spend"
    """The daily vendor budget is nearly gone."""

    CRAWL_FAILED = "crawl_failed"
    """A crawl finished with failures worth a human's attention."""

    @property
    def client_safe(self) -> bool:
        """False for rules that are operational noise to a client."""
        return self not in {AlertRule.ENGINE_SILENT, AlertRule.SPEND, AlertRule.CRAWL_FAILED}


DEFAULT_RULES: tuple[AlertRule, ...] = (AlertRule.CITATION_DROP,)
"""Enabled for a new project. The operator opts into the rest deliberately:
every extra rule is another message somebody has to want to receive."""


class AlertSeverity(StrEnum):
    """How loudly to say it."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertChannel(StrEnum):
    """Where an alert goes."""

    SLACK = "slack"
    EMAIL = "email"


class AlertEvent(StrictModel):
    """One thing worth telling somebody, before any decision to send it."""

    rule: AlertRule
    severity: AlertSeverity = AlertSeverity.WARNING
    title: str = Field(max_length=200)
    detail: str = Field(max_length=1200)
    engine: Engine | None = None
    subject: str = Field(
        default="",
        max_length=200,
        description="What the event is about (a prompt id, a domain). Part of the dedupe key.",
    )
    url: str | None = Field(default=None, description="Evidence a reader can open.")
    numbers: dict[str, float] = Field(default_factory=dict)

    @property
    def dedupe_key(self) -> str:
        """Same rule, same platform, same subject: the same alert."""
        return f"{self.rule.value}|{self.engine.value if self.engine else '-'}|{self.subject}"


class AlertDestination(StrictModel):
    """Where a project's alerts go. Stored, never returned."""

    project_id: str
    enabled: bool = False
    slack_webhook: str | None = Field(
        default=None, description="Incoming webhook URL. A credential: never returned by the API."
    )
    email_to: list[str] = Field(default_factory=list, max_length=10)
    rules: list[AlertRule] = Field(default_factory=lambda: list(DEFAULT_RULES))
    updated_at: datetime | None = None


class AlertDestinationView(StrictModel):
    """What a reader is allowed to know about a destination."""

    project_id: str
    enabled: bool
    slack_configured: bool = False
    slack_hint: str | None = Field(
        default=None, description="Masked, e.g. 'hooks.slack.com/services/T04…'."
    )
    email_count: int = Field(default=0, ge=0)
    email_hints: list[str] = Field(
        default_factory=list, description="Masked recipients, e.g. 'p***@client.com'."
    )
    rules: list[AlertRule] = Field(default_factory=list)
    updated_at: datetime | None = None


class AlertDestinationUpdate(StrictModel):
    """An owner's change to a destination. Every field is optional."""

    enabled: bool | None = None
    slack_webhook: str | None = Field(
        default=None, description="An empty string clears the stored webhook."
    )
    email_to: list[str] | None = Field(default=None, max_length=10)
    rules: list[AlertRule] | None = None


class AlertRecord(StrictModel):
    """One fired event, written before it is sent."""

    id: str = Field(min_length=8)
    project_id: str
    rule: AlertRule
    severity: AlertSeverity
    dedupe_key: str
    title: str
    detail: str
    engine: Engine | None = None
    url: str | None = None
    fired_at: datetime
    channels: list[AlertChannel] = Field(default_factory=list)
    delivered: bool = False
    suppressed_reason: str | None = Field(
        default=None,
        description="Why it was not sent: cooldown, daily_limit, no_destination, rule_off.",
    )
    error: str | None = None
