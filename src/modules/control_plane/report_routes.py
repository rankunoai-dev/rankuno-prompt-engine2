"""Routes for executive reports, brand logos and alert destinations (ADR 0024).

A router rather than more lines in `app.py`. Three things here are security
decisions rather than plumbing:

* **Generating, uploading and configuring are owner writes.** Reading the list
  of reports is open like every other read (ADR 0019), but a report costs
  money to write and an alert destination is a credential.
* **A logo is decoded before it is stored.** Size is checked before the bytes
  are parsed, then Pillow verifies the image really is a PNG or JPEG and
  bounds its pixel dimensions, so a "logo" cannot be a decompression bomb.
* **A download is served from a path this process built** from the report's
  own id, never from anything the caller sent.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image, UnidentifiedImageError

from src.core.config import Settings
from src.core.logger import get_logger
from src.modules.alerting.schemas import AlertDestinationUpdate, AlertDestinationView, AlertRecord
from src.modules.alerting.store import AlertStore
from src.modules.control_plane.project_access import ProjectAccessGuard
from src.modules.control_plane.schemas import ProjectUpdate
from src.modules.control_plane.store import ProjectStore
from src.modules.reporting.schemas import ReportRecord, ReportRequest
from src.modules.reporting.store import ReportStore
from src.modules.reporting.worker import QueueFull, ReportWorker

__all__ = ["register_report_routes", "report_title"]

_logger = get_logger("modules.control_plane.report_routes")

_MAX_PIXELS: Final = 4000
_FORMATS: Final = {"PNG": "png", "JPEG": "jpg"}


def _validated_logo(data: bytes, max_bytes: int) -> tuple[bytes, str]:
    """Return the bytes to store and their extension, or raise `ValueError`.

    The size check comes first: `Image.open` on an unbounded upload is the
    decompression bomb, so it never sees more bytes than the operator allowed.
    """
    if len(data) > max_bytes:
        msg = f"Logo is {len(data)} bytes; the ceiling is {max_bytes}."
        raise ValueError(msg)
    try:
        image = Image.open(io.BytesIO(data))
        image.verify()
    except (UnidentifiedImageError, OSError, ValueError) as error:
        msg = "Logo must be a PNG or JPEG image."
        raise ValueError(msg) from error
    fmt = (image.format or "").upper()
    if fmt not in _FORMATS:
        msg = f"Logo must be PNG or JPEG; this is {fmt or 'unrecognised'}."
        raise ValueError(msg)
    width, height = image.size
    if width > _MAX_PIXELS or height > _MAX_PIXELS:
        msg = f"Logo is {width}x{height}px; the ceiling is {_MAX_PIXELS}px on a side."
        raise ValueError(msg)
    return data, _FORMATS[fmt]


def report_title(body: ReportRequest, now: datetime | None = None) -> str:
    """The stored title: what the operator asked for, or the month stamp."""
    if body.title:
        return body.title[:120]
    return f"AI visibility report — {(now or datetime.now(UTC)):%B %Y}"


def register_report_routes(
    app: FastAPI,
    *,
    store: ProjectStore,
    reports: ReportStore,
    worker: ReportWorker,
    alerts: AlertStore,
    guard: ProjectAccessGuard,
    settings: Callable[[], Settings],
) -> None:
    """Install the report, branding and alert routes."""
    owner_only = [Depends(guard.require_write)]

    @app.exception_handler(QueueFull)
    async def _queue_full(_: Request, exc: QueueFull) -> JSONResponse:
        return JSONResponse(status_code=429, content={"detail": str(exc)})

    # -- reports -----------------------------------------------------------

    @app.post(
        "/api/projects/{project_id}/reports",
        response_model=ReportRecord,
        status_code=202,
        dependencies=owner_only,
    )
    async def create_report(project_id: str, body: ReportRequest) -> ReportRecord:
        """Queue a report. Returns at once; poll the row for its state."""
        project = store.get_project(project_id)
        record = reports.create(project_id, body, project.brand, report_title(body))
        worker.submit(record)
        _logger.info(
            "report_queued",
            extra={
                "project_id": project_id,
                "report_id": record.id,
                "narrative": body.narrative,
                "recipients": len(body.email_to),
            },
        )
        return record

    @app.get("/api/projects/{project_id}/reports", response_model=list[ReportRecord])
    async def list_reports(
        project_id: str, limit: int = Query(default=50, ge=1, le=200)
    ) -> list[ReportRecord]:
        """A project's reports, newest first."""
        store.get_project(project_id)
        return reports.list_for(project_id, limit=limit)

    @app.get("/api/projects/{project_id}/reports/{report_id}", response_model=ReportRecord)
    async def get_report(project_id: str, report_id: str) -> ReportRecord:
        """One report row, for polling while it generates."""
        record = reports.get(report_id)
        if record is None or record.project_id != project_id:
            msg = f"Report {report_id!r} was not found."
            raise KeyError(msg)
        return record

    @app.get("/api/projects/{project_id}/reports/{report_id}/download", response_class=FileResponse)
    async def download_report(project_id: str, report_id: str) -> FileResponse:
        """Serve the PDF from the path this process built for it."""
        record = reports.get(report_id)
        if record is None or record.project_id != project_id:
            msg = f"Report {report_id!r} was not found."
            raise KeyError(msg)
        if not record.downloadable:
            msg = (
                f"Report {report_id!r} has no file to download "
                f"({'purged' if record.purged_at else record.state.value})."
            )
            raise ValueError(msg)
        path = reports.file_path(record)
        if path is None or not path.exists():
            msg = f"Report {report_id!r} is recorded but its file is gone."
            raise ValueError(msg)
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=f"{record.title[:60].replace('/', '-')}.pdf",
        )

    @app.delete(
        "/api/projects/{project_id}/reports/{report_id}", status_code=204, dependencies=owner_only
    )
    async def delete_report(project_id: str, report_id: str) -> None:
        """Delete a report and its file."""
        record = reports.get(report_id)
        if record is None or record.project_id != project_id:
            msg = f"Report {report_id!r} was not found."
            raise KeyError(msg)
        reports.delete(report_id)

    # -- branding ----------------------------------------------------------

    @app.post("/api/projects/{project_id}/branding/logo", dependencies=owner_only)
    async def upload_logo(project_id: str, request: Request) -> dict[str, str]:
        """Store a PNG or JPEG and attach it to the project's brand."""
        project = store.get_project(project_id)
        active = settings()
        raw = await request.body()
        data, suffix = _validated_logo(bytes(raw), active.report_logo_max_bytes)
        logo_id = reports.save_logo(data, suffix)
        brand = project.brand.model_copy(update={"logo_id": logo_id})
        store.update_project(project_id, ProjectUpdate(brand=brand))
        _logger.info(
            "logo_stored", extra={"project_id": project_id, "bytes": len(data), "format": suffix}
        )
        return {"logo_id": logo_id}

    @app.get("/api/projects/{project_id}/branding/logo", response_class=FileResponse)
    async def get_logo(project_id: str) -> FileResponse:
        """Serve the stored logo so the UI can preview what the PDF will show."""
        project = store.get_project(project_id)
        if not project.brand.logo_id:
            msg = "This project has no logo."
            raise KeyError(msg)
        path = reports.logo_path(project.brand.logo_id)
        if not path.exists():
            msg = "The stored logo file is missing."
            raise KeyError(msg)
        suffix = "png" if path.suffix == ".png" else "jpeg"
        return FileResponse(path, media_type=f"image/{suffix}")

    # -- alerts ------------------------------------------------------------

    @app.get("/api/projects/{project_id}/alerts", response_model=AlertDestinationView)
    async def alert_destination(project_id: str) -> AlertDestinationView:
        """The destination, masked: never the webhook, never a full address."""
        store.get_project(project_id)
        return alerts.view(project_id)

    @app.put(
        "/api/projects/{project_id}/alerts",
        response_model=AlertDestinationView,
        dependencies=owner_only,
    )
    async def set_alert_destination(
        project_id: str, body: AlertDestinationUpdate
    ) -> AlertDestinationView:
        """Configure where this project's alerts go."""
        store.get_project(project_id)
        return alerts.save_destination(project_id, body)

    @app.get("/api/projects/{project_id}/alerts/history", response_model=list[AlertRecord])
    async def alert_history(
        project_id: str, limit: int = Query(default=50, ge=1, le=200)
    ) -> list[AlertRecord]:
        """What fired, what was sent, and what was suppressed and why."""
        store.get_project(project_id)
        return alerts.list_for(project_id, limit=limit)
