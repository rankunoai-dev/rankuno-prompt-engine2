"""Per-project write access: everyone reads, only the credential holder writes (ADR 0019).

The site-wide Basic credential (`auth.py`) answers "may this browser reach the
app at all". On a shared deployment everyone holds it, so it cannot also answer
"may this person change *this* project". That is what the owner credential set
at project creation is for.

Design stance:

- Reads never need it. Every mutating route under `/api/projects/{id}` does:
  editing, deleting, prompt changes, imports, the paid `/run`, consolidation and
  action updates.
- It travels in its own header, `X-Project-Authorization: Basic <owner:password>`,
  because `Authorization` already carries the site credential.
- A refusal is **403**, never 401. A 401 makes browsers discard the cached site
  credential and prompt again, which would log the reader out of the whole app.
- A project with no credential stays open, so stores that predate this cycle
  keep working until someone claims each project.
- Wrong guesses are counted per project and answered with 429 past a threshold.
  One process serves the app (ADR 0018), so an in-memory counter is sufficient.
"""

from __future__ import annotations

import secrets
import time
from collections import deque
from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.core.config import Settings
from src.core.logger import get_logger
from src.modules.control_plane.auth import parse_basic
from src.modules.control_plane.credentials import hash_password, verify_password
from src.modules.control_plane.schemas import ProjectAccess, ProjectCredentials
from src.modules.control_plane.store import ProjectStore

__all__ = [
    "PROJECT_AUTH_HEADER",
    "AttemptLimiter",
    "ProjectAccessGuard",
    "ProjectLocked",
    "TooManyAttempts",
    "register_project_access",
]

_logger = get_logger("modules.control_plane.project_access")

PROJECT_AUTH_HEADER = "X-Project-Authorization"
_MAX_FAILURES = 8
_WINDOW_SECONDS = 300.0


class ProjectLocked(Exception):
    """A write reached a protected project without its credential."""

    def __init__(self, project_id: str, owner: str | None, *, presented: bool) -> None:
        """Record whether a (wrong) credential was presented or none at all."""
        super().__init__(project_id)
        self.project_id = project_id
        self.owner = owner
        self.presented = presented


class TooManyAttempts(Exception):
    """Too many wrong credentials for one project inside the window."""

    def __init__(self, retry_after: int) -> None:
        """Carry the seconds until the oldest counted failure expires."""
        super().__init__(retry_after)
        self.retry_after = retry_after


class AttemptLimiter:
    """Sliding-window count of wrong credentials, per project."""

    def __init__(
        self,
        max_failures: int = _MAX_FAILURES,
        window_seconds: float = _WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Allow `max_failures` wrong guesses per project per `window_seconds`."""
        self._max = max_failures
        self._window = window_seconds
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}

    def retry_after(self, project_id: str) -> int | None:
        """Seconds to wait when the project is throttled, else `None`."""
        now = self._clock()
        failures = self._failures.get(project_id)
        if not failures:
            return None
        while failures and now - failures[0] >= self._window:
            failures.popleft()
        if len(failures) < self._max:
            return None
        return max(1, int(self._window - (now - failures[0])))

    def record_failure(self, project_id: str) -> None:
        """Count one wrong credential."""
        self._failures.setdefault(project_id, deque()).append(self._clock())

    def reset(self, project_id: str) -> None:
        """Forget the failures of a project after a correct credential."""
        self._failures.pop(project_id, None)


class ProjectAccessGuard:
    """Decides whether the presented credential may write to a project."""

    def __init__(
        self,
        store: ProjectStore,
        settings: Callable[[], Settings],
        limiter: AttemptLimiter | None = None,
    ) -> None:
        """Bind the credential store, the recovery password source and the throttle."""
        self._store = store
        self._settings = settings
        self._limiter = limiter or AttemptLimiter()

    def evaluate(self, project_id: str, header: str | None) -> ProjectAccess:
        """What the caller may do. `KeyError` for an unknown project.

        Raises `TooManyAttempts` while the project is throttled; a wrong
        credential is counted here, so `/access` cannot serve as a free oracle.
        """
        record = self._store.credential_record(project_id)
        if record is None:
            return ProjectAccess(protected=False, owner=None, can_write=True)
        presented = parse_basic(header)
        if presented is None:
            return ProjectAccess(protected=True, owner=record.owner, can_write=False)
        wait = self._limiter.retry_after(project_id)
        if wait is not None:
            raise TooManyAttempts(wait)
        owner, password = presented
        allowed = verify_password(record, owner, password) or self._is_admin(password)
        if allowed:
            self._limiter.reset(project_id)
        else:
            self._limiter.record_failure(project_id)
            _logger.info("project_credential_rejected", extra={"project_id": project_id})
        return ProjectAccess(protected=True, owner=record.owner, can_write=allowed)

    def _is_admin(self, password: str) -> bool:
        admin = self._settings().project_admin_secret
        return admin is not None and secrets.compare_digest(password.encode(), admin.encode())

    def require_write(self, project_id: str, request: Request) -> None:
        """FastAPI dependency for every mutating project route.

        Deliberately synchronous: scrypt is CPU-bound, and FastAPI runs sync
        dependencies in its thread pool instead of on the event loop.
        """
        header = request.headers.get(PROJECT_AUTH_HEADER)
        access = self.evaluate(project_id, header)
        if not access.can_write:
            _logger.info(
                "project_write_denied",
                extra={
                    "project_id": project_id,
                    "path": request.url.path,
                    "presented": header is not None,
                },
            )
            raise ProjectLocked(project_id, access.owner, presented=header is not None)


def register_project_access(
    app: FastAPI, store: ProjectStore, settings: Callable[[], Settings]
) -> ProjectAccessGuard:
    """Install the access routes and error handlers; return the guard for write routes."""
    guard = ProjectAccessGuard(store, settings)

    @app.exception_handler(ProjectLocked)
    async def _locked(_: Request, exc: ProjectLocked) -> JSONResponse:
        code = "project_credentials_invalid" if exc.presented else "project_locked"
        detail = (
            "Wrong owner name or password for this project."
            if exc.presented
            else "This project is read-only. Unlock it with the owner credential to make changes."
        )
        return JSONResponse(
            status_code=403,
            content={"detail": detail, "code": code, "owner": exc.owner},
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(TooManyAttempts)
    async def _throttled(_: Request, exc: TooManyAttempts) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={
                "detail": "Too many wrong credentials for this project. Try again later.",
                "code": "project_unlock_throttled",
            },
            headers={"Retry-After": str(exc.retry_after), "Cache-Control": "no-store"},
        )

    @app.get("/api/projects/{project_id}/access", response_model=ProjectAccess)
    def project_access(project_id: str, request: Request) -> ProjectAccess:
        return guard.evaluate(project_id, request.headers.get(PROJECT_AUTH_HEADER))

    @app.put("/api/projects/{project_id}/credentials", response_model=ProjectAccess)
    def set_project_credentials(
        project_id: str, body: ProjectCredentials, request: Request
    ) -> ProjectAccess:
        # An open project may be claimed by whoever can already write to it, which
        # is everyone; a protected one only by its current holder (or recovery).
        guard.require_write(project_id, request)
        record = hash_password(body.owner, body.password.get_secret_value())
        store.set_credentials(project_id, record)
        return ProjectAccess(protected=True, owner=record.owner, can_write=True)

    return guard
