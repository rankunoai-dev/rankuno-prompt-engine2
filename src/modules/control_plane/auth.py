"""HTTP Basic authentication for the control plane.

The app was built to bind to loopback, and that bind was its only access
control. Once it sits on a public URL, every route — project CRUD, the paid
`/run`, the full answer archive, hard deletes — needs a credential. Basic auth
is the smallest mechanism that covers the served React UI too: the browser
prompts once and attaches the credential to every same-origin fetch, so the
front end needs no change.

Implemented as a pure ASGI wrapper rather than a FastAPI dependency so that the
static UI, `/assets`, `/docs` and the atlas-data routes are covered as well, not
only the JSON routes. Exactly one path is exempt: `/api/health`, which the
platform's health checker calls without credentials.
"""

from __future__ import annotations

import base64
import binascii
import secrets
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from src.core.logger import get_logger

__all__ = ["BasicAuthMiddleware", "HEALTH_PATH", "credentials_match"]

_logger = get_logger("modules.control_plane.auth")

HEALTH_PATH = "/api/health"
_REALM = b'Basic realm="Prompt Engine", charset="UTF-8"'

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def credentials_match(header: str | None, user: str, password: str) -> bool:
    """True when `header` is a well-formed Basic credential equal to `user:password`.

    Both halves are compared with `secrets.compare_digest`, and a malformed
    header is a plain mismatch rather than an exception.
    """
    if not header:
        return False
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return False
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return False
    given_user, sep, given_password = decoded.partition(":")
    if not sep:
        return False
    user_ok = secrets.compare_digest(given_user.encode(), user.encode())
    password_ok = secrets.compare_digest(given_password.encode(), password.encode())
    return user_ok and password_ok


class BasicAuthMiddleware:
    """Require Basic auth on every HTTP request except the health path."""

    def __init__(self, app: ASGIApp, *, user: str, password: str) -> None:
        """Wrap `app`, protecting it with the given credential."""
        self._app = app
        self._user = user
        self._password = password

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Pass through authenticated HTTP requests; answer others with 401."""
        if scope.get("type") != "http" or scope.get("path") == HEALTH_PATH:
            await self._app(scope, receive, send)
            return
        header = next(
            (v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"authorization"),
            None,
        )
        if credentials_match(header, self._user, self._password):
            await self._app(scope, receive, send)
            return
        _logger.info(
            "auth_rejected",
            extra={"path": scope.get("path"), "presented": header is not None},
        )
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"www-authenticate", _REALM),
                    (b"content-type", b"application/json"),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b'{"detail":"Authentication required"}'})
