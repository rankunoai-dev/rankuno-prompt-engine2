"""Outbound email over SMTP (ADR 0024).

The one connector in this package that is not HTTP, so it is deliberately not
a `BaseAPIClient`: there is no status code to classify, no rate-limit header to
read, and no per-call vendor price to book. It keeps the same shape as the
others — construct from `Settings`, one operation, structured logging, errors
raised as `IntegrationError` — so callers treat it like any other integration.

SMTP rather than a provider API because the operator already pays for a
mailbox, and a report going to a client should leave from the agency's own
domain rather than a third party's sending infrastructure.

Recipients are personal data. They are never written to a log line in full;
`_mask` keeps the first character and the domain, which is enough to debug a
delivery and useless to a log reader.
"""

from __future__ import annotations

import re
import smtplib
import ssl
from collections.abc import Callable, Sequence
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from typing import Final

from src.core.config import Settings, get_settings
from src.core.errors import IntegrationError
from src.core.logger import get_logger

__all__ = ["Attachment", "EmailClient", "valid_address"]

_logger = get_logger("integrations.email_send")

_ADDRESS: Final = re.compile(r"^[^@\s,;<>]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_MAX_ATTACHMENT_BYTES: Final = 20_000_000
_MAX_RECIPIENTS: Final = 10


class Attachment:
    """One file to send. A value object, not a model: it carries raw bytes."""

    def __init__(self, filename: str, data: bytes, mime: str = "application/pdf") -> None:
        """Keep the parts `EmailMessage.add_attachment` needs."""
        if len(data) > _MAX_ATTACHMENT_BYTES:
            msg = f"attachment is {len(data)} bytes; the ceiling is {_MAX_ATTACHMENT_BYTES}."
            raise ValueError(msg)
        self.filename = filename
        self.data = data
        self.maintype, _, self.subtype = mime.partition("/")


def valid_address(value: str) -> bool:
    """True when `value` is an address this module is willing to send to."""
    _, address = parseaddr(value)
    return bool(_ADDRESS.match(address))


def _mask(address: str) -> str:
    """`priya@client.com` becomes `p***@client.com` for the audit log."""
    local, _, domain = address.partition("@")
    if not domain:
        return "***"
    return f"{local[:1]}***@{domain}"


class EmailClient:
    """Sends one message per call over SMTP."""

    service_name = "smtp"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        factory: Callable[[str, int, float], smtplib.SMTP] | None = None,
    ) -> None:
        """`factory` builds the SMTP connection; tests inject a fake."""
        self._settings = settings or get_settings()
        self._factory = factory or self._default_factory

    @staticmethod
    def _default_factory(host: str, port: int, timeout: float) -> smtplib.SMTP:
        """A real SMTP connection."""
        return smtplib.SMTP(host, port, timeout=timeout)

    @property
    def configured(self) -> bool:
        """True when a host and a sender are set; email is optional everywhere."""
        return bool(self._settings.smtp_host and self._settings.smtp_from)

    def send(
        self,
        *,
        to: Sequence[str],
        subject: str,
        body: str,
        attachments: Sequence[Attachment] = (),
        display_name: str | None = None,
    ) -> int:
        """Send to every valid recipient and return how many were accepted.

        Invalid addresses are dropped with a log line rather than failing the
        send: one typo in a client list must not stop the other four people
        receiving their report.
        """
        if not self.configured:
            msg = "not configured; set SMTP_HOST and SMTP_FROM"
            raise IntegrationError(self.service_name, msg)
        recipients = [address for address in to if valid_address(address)][:_MAX_RECIPIENTS]
        dropped = len(to) - len(recipients)
        if not recipients:
            _logger.warning("email_no_valid_recipients", extra={"given": len(to)})
            return 0
        sender = self._settings.smtp_from or ""
        message = EmailMessage()
        message["From"] = formataddr((display_name, sender)) if display_name else sender
        message["To"] = ", ".join(recipients)
        message["Subject"] = subject[:200]
        message.set_content(body)
        for attachment in attachments:
            message.add_attachment(
                attachment.data,
                maintype=attachment.maintype,
                subtype=attachment.subtype,
                filename=attachment.filename,
            )
        host = self._settings.smtp_host or ""
        try:
            with self._factory(
                host, self._settings.smtp_port, self._settings.smtp_timeout_s
            ) as smtp:
                if self._settings.smtp_starttls:
                    smtp.starttls(context=ssl.create_default_context())
                user = self._settings.smtp_user
                password = self._settings.smtp_password
                if user and password:
                    smtp.login(user, password.get_secret_value())
                smtp.send_message(message)
        except (smtplib.SMTPException, OSError, ssl.SSLError) as error:
            msg = f"delivery failed: {type(error).__name__}: {error}"
            raise IntegrationError(self.service_name, msg) from error
        _logger.info(
            "email_sent",
            extra={
                "recipients": [_mask(address) for address in recipients],
                "dropped": dropped,
                "attachments": len(attachments),
                "subject": subject[:80],
            },
        )
        return len(recipients)
