"""Slack delivery through an incoming webhook (ADR 0024).

An incoming webhook rather than a Slack app: the operator pastes one URL per
project, there are no OAuth scopes to review, no tokens to refresh, and the
blast radius of a leaked URL is "someone can post into one channel" rather
than "someone can read a workspace".

The URL *is* the credential, so it is never logged, never returned by the API
and never put in an error message; `describe()` is the only rendering of it
this application performs.

Cost: none. The call still goes through `BaseAPIClient` for the rate limiter,
the circuit breaker and the audit trail — an alerting path that hammers a
broken endpoint is exactly what the breaker exists for.
"""

from __future__ import annotations

from typing import Any, ClassVar, Final
from urllib.parse import urlparse

import httpx

from src.core.config import Settings
from src.core.errors import IntegrationError
from src.core.logger import get_logger
from src.integrations.base_client import BaseAPIClient
from src.integrations.http import check_response, json_client

__all__ = ["SLACK_HOST", "SlackWebhookClient", "describe_webhook", "valid_webhook"]

_logger = get_logger("integrations.slack")

SLACK_HOST: Final = "hooks.slack.com"
_MAX_TEXT: Final = 2800


def valid_webhook(url: str) -> bool:
    """True for `https://hooks.slack.com/services/...` and nothing else.

    Pinning the host is what stops a project's "Slack destination" from being
    turned into an arbitrary outbound request by whoever can write to it.
    """
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == SLACK_HOST
        and parsed.path.startswith("/services/")
        and len(parsed.path) > len("/services/")
    )


def describe_webhook(url: str) -> str:
    """A safe rendering for the UI: `hooks.slack.com/services/T04…`."""
    parsed = urlparse(url.strip())
    tail = parsed.path.removeprefix("/services/").split("/")[0][:4]
    return f"{SLACK_HOST}/services/{tail}…"


class SlackWebhookClient(BaseAPIClient):
    """Posts one message per call to a caller-supplied webhook."""

    service_name: ClassVar[str] = "slack"
    rate_limit_key: ClassVar[str] = "slack.webhook"
    requests_per_minute: ClassVar[int] = 60

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Build a client; the webhook URL is passed per call, not stored."""
        super().__init__(settings)
        self._transport = transport
        self._http: httpx.Client | None = None

    def authenticate(self) -> None:
        """Create the HTTP session. The credential travels in the URL."""
        self._http = json_client(
            self._settings.default_timeout_s,
            headers={"Content-Type": "application/json"},
            transport=self._transport,
        )

    def post(self, webhook: str, *, text: str, blocks: list[dict[str, Any]] | None = None) -> None:
        """Deliver one message. Raises `IntegrationError` on refusal."""
        if not valid_webhook(webhook):
            msg = "Slack destination must be an https://hooks.slack.com/services/... URL."
            raise IntegrationError(msg)
        if self._http is None:
            self.authenticate()
        assert self._http is not None  # noqa: S101 - narrowed by authenticate()
        payload: dict[str, Any] = {"text": text[:_MAX_TEXT]}
        if blocks:
            payload["blocks"] = blocks

        def request() -> None:
            response = self._http.post(webhook, json=payload)  # type: ignore[union-attr]
            check_response(self.service_name, response)

        self.call("post_message", request, estimated_cost_usd=0.0)
        _logger.info("slack_posted", extra={"destination": describe_webhook(webhook)})
