"""Routes for crawler-log imports and the fetch-to-citation funnel (ADR 0022).

A router rather than more lines in `app.py`. The import route accepts either the
browser's JSON text (small, pre-filtered) or a raw streamed body (`text/plain`,
NDJSON or gzip) for real files; the raw path spools to the container's
temporary directory — never the data volume — with the cap enforced on
decompressed bytes, parses in the thread pool, and deletes the spool.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Iterator
from typing import IO

from fastapi import Depends, FastAPI, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from pydantic import Field

from src.core.config import Settings
from src.core.logger import get_logger
from src.core.schemas import StrictModel
from src.modules.control_plane.project_access import ProjectAccessGuard
from src.modules.control_plane.store import ProjectStore
from src.modules.crawler_logs.bots import catalogue_out
from src.modules.crawler_logs.funnel import build_view
from src.modules.crawler_logs.ingest import aggregate
from src.modules.crawler_logs.parser import ParseStats, PayloadTooLarge, iter_hits
from src.modules.crawler_logs.ranges import bundled_ranges
from src.modules.crawler_logs.schemas import BotSpecOut, CrawlerImportResult, CrawlerLogView
from src.modules.crawler_logs.store import CrawlerLogStore, DuplicateImport
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB

__all__ = ["CrawlerImportBody", "register_crawler_routes"]

_logger = get_logger("modules.control_plane.crawler_routes")
_CHUNK = 1 << 16
_SPOOL_IN_MEMORY = 8 << 20


class CrawlerImportBody(StrictModel):
    """A log posted as text by the browser after client-side pre-filtering."""

    text: str = Field(min_length=1)
    note: str = Field(default="", max_length=500)


def _chunks(handle: IO[bytes]) -> Iterator[bytes]:
    while True:
        chunk = handle.read(_CHUNK)
        if not chunk:
            return
        yield chunk


def register_crawler_routes(
    app: FastAPI,
    *,
    store: ProjectStore,
    db: TimeSeriesDB,
    crawler_logs: CrawlerLogStore,
    guard: ProjectAccessGuard,
    settings: Callable[[], Settings],
) -> None:
    """Install the four routes and the two error mappings they need."""
    owner_only = [Depends(guard.require_write)]

    @app.exception_handler(DuplicateImport)
    async def _duplicate(_: Request, exc: DuplicateImport) -> JSONResponse:
        return JSONResponse(
            status_code=409, content={"detail": str(exc), "import_id": exc.import_id}
        )

    @app.exception_handler(PayloadTooLarge)
    async def _too_large(_: Request, exc: PayloadTooLarge) -> JSONResponse:
        return JSONResponse(status_code=413, content={"detail": str(exc)})

    def _ingest(project_id: str, spool: IO[bytes], note: str) -> CrawlerImportResult:
        active = settings()
        project = store.get_project(project_id)
        stats = ParseStats()
        spool.seek(0)
        hits = iter_hits(
            _chunks(spool),
            stats,
            max_bytes=active.crawler_log_max_bytes,
            max_json_bytes=active.crawler_log_max_json_bytes,
        )
        result = aggregate(
            hits, client_domains=list(project.client.domains), ranges=bundled_ranges(), stats=stats
        )
        record = crawler_logs.record_import(project_id, result, note=note)
        crawler_logs.purge(active.crawler_log_retention_days)
        return CrawlerImportResult(
            import_id=record.id,
            format=stats.format,
            lines=stats.lines,
            parsed=stats.parsed,
            unparsed=stats.unparsed,
            duplicate_lines=stats.duplicate_lines,
            matched=result.matched,
            hits=result.hits,
            verified_hits=result.verified_hits,
            stealth_hits=result.stealth_hits,
            hosts_skipped=result.hosts_skipped,
            no_host=result.no_host,
            methods_skipped=stats.methods_skipped,
            sensitive_dropped=result.sensitive_dropped,
            keys_truncated=result.keys_truncated,
            span_from=record.span_from,
            span_to=record.span_to,
            verification_basis=result.verification_basis,
            sampled=stats.sampled,
            overlaps=record.overlaps,
        )

    @app.post(
        "/api/projects/{project_id}/crawler-logs/import",
        response_model=CrawlerImportResult,
        dependencies=owner_only,
    )
    async def import_log(project_id: str, request: Request) -> CrawlerImportResult:
        """Import one access log. JSON `{"text"}` or a raw text/gzip body."""
        store.get_project(project_id)
        active = settings()
        content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > active.crawler_log_max_bytes:
            raise PayloadTooLarge(f"Log exceeds {active.crawler_log_max_bytes} bytes.")
        note = ""
        with tempfile.SpooledTemporaryFile(max_size=_SPOOL_IN_MEMORY) as spool:
            if content_type == "application/json":
                raw = await request.body()
                if len(raw) > active.crawler_log_max_json_bytes:
                    raise PayloadTooLarge(
                        "JSON text is limited to "
                        f"{active.crawler_log_max_json_bytes} bytes; upload the file as text/plain."
                    )
                body = CrawlerImportBody.model_validate_json(raw)
                note = body.note
                spool.write(body.text.encode("utf-8"))
            else:
                total = 0
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > active.crawler_log_max_bytes:
                        raise PayloadTooLarge(f"Log exceeds {active.crawler_log_max_bytes} bytes.")
                    spool.write(chunk)
            if spool.tell() == 0:
                msg = "The request body is empty."
                raise ValueError(msg)
            return await run_in_threadpool(_ingest, project_id, spool, note)

    @app.get("/api/projects/{project_id}/crawler-logs", response_model=CrawlerLogView)
    async def crawler_logs_view(
        project_id: str, days: int = Query(default=30, ge=1, le=400)
    ) -> CrawlerLogView:
        """Per-bot activity, daily coverage and the fetch-to-citation funnel."""
        project = store.get_project(project_id)
        prompts = store.list_prompts(project_id)
        view = await run_in_threadpool(
            build_view,
            project_id=project_id,
            client_domains=list(project.client.domains),
            engines=list(project.engines),
            prompt_ids=[p.prompt_id for p in prompts],
            store=crawler_logs,
            db=db,
            days=days,
        )
        return view.model_copy(update={"ranges": bundled_ranges().snapshot()})

    @app.get("/api/crawler-logs/bots", response_model=list[BotSpecOut])
    async def crawler_bots() -> list[BotSpecOut]:
        """The crawler catalogue, with the tokens a browser can pre-filter on."""
        return catalogue_out()

    @app.delete(
        "/api/projects/{project_id}/crawler-logs/imports/{import_id}",
        status_code=204,
        dependencies=owner_only,
    )
    async def delete_import(project_id: str, import_id: str) -> Response:
        store.get_project(project_id)
        crawler_logs.delete_import(project_id, import_id)
        _logger.info(
            "crawler_import_deleted", extra={"project_id": project_id, "import_id": import_id}
        )
        return Response(status_code=204)
