"""Destinations, the three gates, and delivery — without touching a network."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.core.config import Settings
from src.core.errors import IntegrationError
from src.integrations.schemas import Engine
from src.modules.alerting.dispatch import AlertDispatcher, email_body, slack_blocks
from src.modules.alerting.schemas import (
    DEFAULT_RULES,
    AlertChannel,
    AlertDestinationUpdate,
    AlertEvent,
    AlertRule,
    AlertSeverity,
)
from src.modules.alerting.store import AlertStore

PROJECT = "proj0001"
HOOK = "https://hooks.slack.com/services/T04AB/B05CD/xyz123secret"
NOW = datetime(2026, 9, 24, 9, tzinfo=UTC)


@pytest.fixture
def store(tmp_path) -> AlertStore:
    return AlertStore(tmp_path / "cp.sqlite")


class FakeSlack:
    """Captures posts; can be told to fail."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.posts: list[tuple[str, str]] = []

    def post(self, webhook: str, *, text: str, blocks=None) -> None:
        if self.error:
            raise self.error
        self.posts.append((webhook, text))


class FakeMailer:
    """Captures sends; can be told it is unconfigured or broken."""

    def __init__(self, *, configured: bool = True, error: Exception | None = None) -> None:
        self.configured = configured
        self.error = error
        self.sent: list[dict[str, object]] = []

    def send(self, **kwargs: object) -> int:
        if self.error:
            raise self.error
        self.sent.append(kwargs)
        to = kwargs.get("to")
        return len(to) if isinstance(to, list) else 0


def event(rule: AlertRule = AlertRule.CITATION_DROP, *, subject: str = "CHATGPT_SEARCH"):
    """One candidate event."""
    return AlertEvent(
        rule=rule,
        severity=AlertSeverity.WARNING,
        title="ChatGPT Search: citation rate fell 60 points",
        detail="80% before, 20% now. The 95% intervals do not overlap.",
        engine=Engine.CHATGPT_SEARCH,
        subject=subject,
        url="https://www.g2.com/categories/procurement",
    )


def _dispatcher(store, **kwargs) -> AlertDispatcher:
    """A dispatcher with fakes wired in by default.

    Never leave `slack` or `mailer` unset here: the dispatcher builds the real
    client when one is missing, and `HOOK` is a real Slack host, so a bare
    dispatcher in a test posts to hooks.slack.com for real.
    """
    settings = kwargs.pop("settings", Settings(alerts_max_per_project_per_day=5))
    clock = kwargs.pop("clock", lambda: NOW)
    kwargs.setdefault("slack", FakeSlack())
    kwargs.setdefault("mailer", FakeMailer(configured=False))
    return AlertDispatcher(store, settings=settings, clock=clock, **kwargs)


def _enable(store, **kwargs) -> None:
    store.save_destination(
        PROJECT, AlertDestinationUpdate(slack_webhook=HOOK, enabled=True, **kwargs)
    )


# -- destinations ---------------------------------------------------------


def test_a_new_project_has_only_the_drop_rule_and_no_destination(store):
    """The operator opts into every other rule deliberately."""
    destination = store.destination(PROJECT)
    assert destination.enabled is False
    assert tuple(destination.rules) == DEFAULT_RULES == (AlertRule.CITATION_DROP,)
    assert destination.slack_webhook is None


def test_the_webhook_and_the_recipients_never_leave_as_written(store):
    """A webhook is a credential and an address is personal data."""
    store.save_destination(
        PROJECT,
        AlertDestinationUpdate(slack_webhook=HOOK, email_to=["priya@client.com"], enabled=True),
    )
    view = store.view(PROJECT)
    dumped = view.model_dump_json()
    assert "xyz123secret" not in dumped
    assert "priya@client.com" not in dumped
    assert view.slack_configured is True
    assert view.slack_hint == "hooks.slack.com/services/T04A…"
    assert view.email_hints == ["p***@client.com"]
    assert view.email_count == 1


def test_a_destination_that_is_not_slack_is_refused(store):
    """Otherwise 'alerts' becomes an arbitrary outbound request on a schedule."""
    for hostile in (
        "https://evil.example.com/hook",
        "http://hooks.slack.com/services/x",
        "nonsense",
    ):
        with pytest.raises(ValueError, match="hooks.slack.com"):
            store.save_destination(PROJECT, AlertDestinationUpdate(slack_webhook=hostile))


def test_a_bad_address_is_refused_and_an_empty_webhook_clears_it(store):
    """Validation at the boundary, so the sender never has to guess."""
    with pytest.raises(ValueError, match="valid email"):
        store.save_destination(PROJECT, AlertDestinationUpdate(email_to=["not-an-address"]))

    _enable(store)
    store.save_destination(PROJECT, AlertDestinationUpdate(slack_webhook="", email_to=["a@b.com"]))
    assert store.destination(PROJECT).slack_webhook is None
    assert store.view(PROJECT).slack_configured is False


def test_enabling_alerts_with_nowhere_to_send_them_is_refused(store):
    """A switch that does nothing is worse than no switch."""
    with pytest.raises(ValueError, match="before enabling"):
        store.save_destination(PROJECT, AlertDestinationUpdate(enabled=True))


def test_deleting_a_project_forgets_its_destination_and_history(store):
    """Credentials and personal data go with the project."""
    _enable(store)
    _dispatcher(store, slack=FakeSlack()).dispatch(PROJECT, "GEP", [event()])
    store.delete_project(PROJECT)
    assert store.destination(PROJECT).slack_webhook is None
    assert store.list_for(PROJECT) == []


# -- the gates ------------------------------------------------------------


def test_an_enabled_rule_with_a_destination_is_delivered_and_recorded(store):
    """The happy path, end to end, with the history showing what was sent."""
    _enable(store)
    slack = FakeSlack()
    records = _dispatcher(store, slack=slack).dispatch(PROJECT, "GEP", [event()])
    assert len(records) == 1
    assert records[0].delivered is True
    assert records[0].channels == [AlertChannel.SLACK]
    assert slack.posts[0][0] == HOOK
    assert "citation rate fell" in slack.posts[0][1]
    assert store.list_for(PROJECT)[0].delivered is True


def test_alerts_off_a_rule_off_and_no_destination_are_all_recorded_with_a_reason(store):
    """'Why didn't I hear about this?' has an answer in the history."""
    quiet = _dispatcher(store).dispatch(PROJECT, "GEP", [event()])
    assert quiet[0].delivered is False
    assert quiet[0].suppressed_reason == "alerts_off"

    _enable(store, rules=[AlertRule.NEGATIVE_CLAIM])
    wrong_rule = _dispatcher(store).dispatch(PROJECT, "GEP", [event()])
    assert wrong_rule[0].suppressed_reason == "rule_off"

    store.save_destination(PROJECT, AlertDestinationUpdate(slack_webhook=HOOK, enabled=True))
    store.save_destination(PROJECT, AlertDestinationUpdate(enabled=False))
    store.save_destination(PROJECT, AlertDestinationUpdate(slack_webhook="", email_to=[]))
    silent = _dispatcher(store).dispatch(PROJECT, "GEP", [event()])
    assert silent[0].suppressed_reason in {"alerts_off", "no_destination"}


def test_the_same_alert_stays_quiet_for_the_cooldown_then_fires_again(store):
    """A metric parked past the threshold must not re-announce every crawl."""
    _enable(store)
    slack = FakeSlack()
    first = _dispatcher(store, slack=slack).dispatch(PROJECT, "GEP", [event()])
    assert first[0].delivered is True

    again = _dispatcher(store, slack=slack).dispatch(PROJECT, "GEP", [event()])
    assert again[0].delivered is False
    assert again[0].suppressed_reason == "cooldown"
    assert len(slack.posts) == 1

    later = _dispatcher(store, slack=slack, clock=lambda: NOW + timedelta(hours=73))
    third = later.dispatch(PROJECT, "GEP", [event()])
    assert third[0].delivered is True
    assert len(slack.posts) == 2


def test_a_different_subject_is_a_different_alert(store):
    """The cooldown is per rule, platform and subject — not per project."""
    _enable(store)
    slack = FakeSlack()
    _dispatcher(store, slack=slack).dispatch(PROJECT, "GEP", [event()])
    other = _dispatcher(store, slack=slack).dispatch(PROJECT, "GEP", [event(subject="GEMINI")])
    assert other[0].delivered is True
    assert len(slack.posts) == 2


def test_the_daily_ceiling_caps_a_flapping_metric(store):
    """Two a day means two, however many true events arrive."""
    _enable(store, rules=list(AlertRule))
    slack = FakeSlack()
    dispatcher = _dispatcher(
        store, slack=slack, settings=Settings(alerts_max_per_project_per_day=2)
    )
    events = [event(subject=f"s{index}") for index in range(5)]
    records = dispatcher.dispatch(PROJECT, "GEP", events)
    assert sum(r.delivered for r in records) == 2
    assert [r.suppressed_reason for r in records[2:]] == ["daily_limit"] * 3
    assert len(slack.posts) == 2


def test_a_zero_ceiling_disables_sending_entirely(store):
    """One setting stops every outbound message without touching a project."""
    _enable(store)
    slack = FakeSlack()
    records = _dispatcher(
        store, slack=slack, settings=Settings(alerts_max_per_project_per_day=0)
    ).dispatch(PROJECT, "GEP", [event()])
    assert records[0].suppressed_reason == "sending_disabled"
    assert slack.posts == []


# -- delivery -------------------------------------------------------------


def test_both_channels_are_used_when_both_are_configured(store):
    """Slack for the team, email for whoever does not live in Slack."""
    store.save_destination(
        PROJECT,
        AlertDestinationUpdate(slack_webhook=HOOK, email_to=["priya@client.com"], enabled=True),
    )
    slack, mailer = FakeSlack(), FakeMailer()
    records = _dispatcher(store, slack=slack, mailer=mailer).dispatch(PROJECT, "GEP", [event()])
    assert set(records[0].channels) == {AlertChannel.SLACK, AlertChannel.EMAIL}
    assert mailer.sent[0]["subject"] == "[GEP] ChatGPT Search: citation rate fell 60 points"


def test_one_channel_failing_still_counts_as_delivered_and_records_the_error(store):
    """Partial delivery is delivery; the error is kept for the history."""
    store.save_destination(
        PROJECT,
        AlertDestinationUpdate(slack_webhook=HOOK, email_to=["priya@client.com"], enabled=True),
    )
    records = _dispatcher(
        store,
        slack=FakeSlack(error=IntegrationError("slack", "500")),
        mailer=FakeMailer(),
    ).dispatch(PROJECT, "GEP", [event()])
    assert records[0].delivered is True
    assert records[0].channels == [AlertChannel.EMAIL]
    assert records[0].error is not None and "slack" in records[0].error


def test_every_channel_failing_is_recorded_as_undelivered(store):
    """And the cooldown does not start, so the next crawl can try again."""
    _enable(store)
    dispatcher = _dispatcher(store, slack=FakeSlack(error=IntegrationError("slack", "500")))
    records = dispatcher.dispatch(PROJECT, "GEP", [event()])
    assert records[0].delivered is False
    assert records[0].error is not None

    retry = _dispatcher(store, slack=FakeSlack())
    assert retry.dispatch(PROJECT, "GEP", [event()])[0].delivered is True


def test_email_is_skipped_when_smtp_is_not_configured(store):
    """Alerts still reach Slack; nothing pretends the mail went out."""
    store.save_destination(
        PROJECT, AlertDestinationUpdate(slack_webhook=HOOK, email_to=["a@b.com"], enabled=True)
    )
    records = _dispatcher(store, slack=FakeSlack(), mailer=FakeMailer(configured=False)).dispatch(
        PROJECT, "GEP", [event()]
    )
    assert records[0].channels == [AlertChannel.SLACK]


def test_the_message_carries_the_headline_the_detail_and_the_source(store):
    """What a reader needs without opening the dashboard."""
    blocks = slack_blocks("GEP procurement", event())
    rendered = str(blocks)
    assert "citation rate fell 60 points" in rendered
    assert "GEP procurement" in rendered
    assert "https://www.g2.com/categories/procurement" in rendered

    body = email_body("GEP procurement", event())
    assert body.startswith("ChatGPT Search: citation rate fell 60 points")
    assert "Source: https://www.g2.com/categories/procurement" in body


def test_nothing_to_say_does_nothing(store):
    """No events means no destination lookup and no rows."""
    assert _dispatcher(store).dispatch(PROJECT, "GEP", []) == []
    assert store.list_for(PROJECT) == []
