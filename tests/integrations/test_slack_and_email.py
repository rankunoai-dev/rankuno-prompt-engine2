"""The two outbound channels (ADR 0024). No socket is opened in this file."""

from __future__ import annotations

import json
import smtplib
import ssl
from email.message import EmailMessage

import httpx
import pytest
from pydantic import SecretStr

from src.core.config import Settings
from src.core.errors import IntegrationError, UpstreamClientError
from src.integrations.email_send import Attachment, EmailClient, valid_address
from src.integrations.slack import (
    SlackWebhookClient,
    describe_webhook,
    valid_webhook,
)

HOOK = "https://hooks.slack.com/services/T04AB/B05CD/xyz123secret"


class Recorder:
    """Captures the request and answers with a canned response."""

    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.response


# -- Slack ----------------------------------------------------------------


def test_only_a_slack_incoming_webhook_is_accepted():
    """The host pin is what stops this becoming an arbitrary outbound request."""
    assert valid_webhook(HOOK) is True
    for hostile in (
        "https://evil.example.com/services/x",
        "http://hooks.slack.com/services/x",  # not https
        "https://hooks.slack.com/api/chat.postMessage",  # not an incoming webhook
        "https://hooks.slack.com/services/",  # no token
        "https://hooks.slack.com.evil.test/services/x",
        "",
    ):
        assert valid_webhook(hostile) is False


def test_the_webhook_is_described_without_revealing_it():
    """The UI shows where alerts go, not the credential that sends them."""
    hint = describe_webhook(HOOK)
    assert hint == "hooks.slack.com/services/T04A…"
    assert "xyz123secret" not in hint


def test_a_message_posts_the_text_and_the_blocks():
    """Slack renders blocks; `text` is the notification fallback."""
    recorder = Recorder(httpx.Response(200, text="ok"))
    client = SlackWebhookClient(Settings(), transport=httpx.MockTransport(recorder))
    client.post(HOOK, text="ChatGPT Search: citation rate fell", blocks=[{"type": "divider"}])
    assert len(recorder.requests) == 1
    body = json.loads(recorder.requests[0].content)
    assert body["text"] == "ChatGPT Search: citation rate fell"
    assert body["blocks"] == [{"type": "divider"}]
    assert str(recorder.requests[0].url) == HOOK


def test_a_bad_destination_never_reaches_the_network():
    """Validation happens before the transport is even built."""
    recorder = Recorder(httpx.Response(200, text="ok"))
    client = SlackWebhookClient(Settings(), transport=httpx.MockTransport(recorder))
    with pytest.raises(IntegrationError, match="hooks.slack.com"):
        client.post("https://evil.example.com/hook", text="hello")
    assert recorder.requests == []


def test_slack_refusing_the_post_raises_for_the_dispatcher_to_record():
    """A dead webhook is an error the alert history keeps."""
    recorder = Recorder(httpx.Response(404, text="no_team"))
    client = SlackWebhookClient(Settings(), transport=httpx.MockTransport(recorder))
    with pytest.raises(UpstreamClientError):
        client.post(HOOK, text="hello")


# -- Email ----------------------------------------------------------------


class FakeSMTP:
    """A stand-in for `smtplib.SMTP` that records what it was asked to do."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.started_tls = False
        self.login_user: str | None = None
        self.messages: list[EmailMessage] = []

    def __call__(self, host: str, port: int, timeout: float) -> FakeSMTP:
        self.host, self.port, self.timeout = host, port, timeout
        return self

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def starttls(self, context: ssl.SSLContext | None = None) -> None:
        if self.fail_on == "starttls":
            raise smtplib.SMTPException("STARTTLS refused")
        self.started_tls = True

    def login(self, user: str, password: str) -> None:
        if self.fail_on == "login":
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")
        self.login_user = user

    def send_message(self, message: EmailMessage) -> None:
        if self.fail_on == "send":
            raise smtplib.SMTPRecipientsRefused({"a@b.com": (550, b"no")})
        self.messages.append(message)


def _settings(**kwargs: object) -> Settings:
    base: dict[str, object] = {
        "smtp_host": "smtp.example.com",
        "smtp_from": "reports@agency.com",
        "smtp_user": "reports@agency.com",
        "smtp_password": SecretStr("app-password"),
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def test_email_is_off_until_a_host_and_a_sender_are_set():
    """Every call site treats email as optional."""
    assert EmailClient(Settings()).configured is False
    assert EmailClient(_settings(smtp_from=None)).configured is False
    assert EmailClient(_settings()).configured is True


def test_a_report_is_sent_as_a_pdf_attachment_over_starttls():
    """The delivery path an operator actually configures."""
    smtp = FakeSMTP()
    client = EmailClient(_settings(), factory=smtp)
    sent = client.send(
        to=["priya@client.com"],
        subject="September report",
        body="Attached.",
        attachments=[Attachment("report.pdf", b"%PDF-1.4 fake")],
        display_name="RankUno",
    )
    assert sent == 1
    assert smtp.started_tls is True
    assert smtp.login_user == "reports@agency.com"
    message = smtp.messages[0]
    assert message["To"] == "priya@client.com"
    assert message["Subject"] == "September report"
    assert message["From"] == "RankUno <reports@agency.com>"
    attachment = next(part for part in message.iter_attachments())
    assert attachment.get_filename() == "report.pdf"
    assert attachment.get_content_type() == "application/pdf"


def test_one_bad_address_does_not_stop_the_others():
    """A typo in a client list must not cost four people their report."""
    smtp = FakeSMTP()
    sent = EmailClient(_settings(), factory=smtp).send(
        to=["priya@client.com", "not-an-address", "sam@client.com"],
        subject="s",
        body="b",
    )
    assert sent == 2
    assert smtp.messages[0]["To"] == "priya@client.com, sam@client.com"


def test_no_valid_recipient_sends_nothing_and_raises_nothing():
    """Zero is a result the caller records, not an exception."""
    smtp = FakeSMTP()
    assert EmailClient(_settings(), factory=smtp).send(to=["nope"], subject="s", body="b") == 0
    assert smtp.messages == []


def test_sending_without_configuration_is_an_integration_error():
    """Callers check `configured`; this is the backstop."""
    with pytest.raises(IntegrationError, match="not configured"):
        EmailClient(Settings()).send(to=["a@b.com"], subject="s", body="b")


@pytest.mark.parametrize("stage", ["starttls", "login", "send"])
def test_every_smtp_failure_surfaces_as_one_error_type(stage: str):
    """The dispatcher and the report worker catch exactly one exception."""
    client = EmailClient(_settings(), factory=FakeSMTP(fail_on=stage))
    with pytest.raises(IntegrationError, match="delivery failed"):
        client.send(to=["a@b.com"], subject="s", body="b")


def test_starttls_can_be_turned_off_for_a_local_relay():
    """Some relays are already encrypted; forcing STARTTLS breaks them."""
    smtp = FakeSMTP()
    EmailClient(_settings(smtp_starttls=False), factory=smtp).send(
        to=["a@b.com"], subject="s", body="b"
    )
    assert smtp.started_tls is False


def test_an_oversized_attachment_is_refused_before_any_connection():
    """20 MB is already generous for a PDF; beyond it something is wrong."""
    with pytest.raises(ValueError, match="ceiling"):
        Attachment("huge.pdf", b"x" * 20_000_001)


def test_address_validation_accepts_real_shapes_and_rejects_the_rest():
    """Used by the destination store before anything is saved."""
    assert valid_address("priya@client.com") is True
    assert valid_address("Priya <priya@client.co.uk>") is True
    for bad in ("priya", "priya@", "@client.com", "a b@c.com", "priya@client", ""):
        assert valid_address(bad) is False


def test_recipients_are_masked_in_the_audit_log(caplog):
    """A log reader learns the delivery happened, not who it reached."""
    with caplog.at_level("INFO"):
        EmailClient(_settings(), factory=FakeSMTP()).send(
            to=["priya@client.com"], subject="s", body="b"
        )
    logged = " ".join(record.getMessage() + str(record.__dict__) for record in caplog.records)
    assert "priya@client.com" not in logged
    assert "p***@client.com" in logged
