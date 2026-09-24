"""The sampling dry run: what the next crawl will do and cost (ADR 0025).

Read-only, so it sits outside the owner guard; the policy itself is a project
field changed through `PUT /api/projects/{id}`.
"""

from __future__ import annotations

from fastapi import FastAPI, Query
from fastapi.concurrency import run_in_threadpool

from src.modules.control_plane.runner import ProjectRunner
from src.modules.control_plane.schemas import SamplingView

__all__ = ["register_sampling_routes"]


def register_sampling_routes(app: FastAPI, *, runner: ProjectRunner) -> None:
    """Install `GET /api/projects/{id}/sampling`."""

    @app.get("/api/projects/{project_id}/sampling", response_model=SamplingView)
    async def sampling(
        project_id: str,
        policy: str | None = Query(default=None, pattern="^(fixed|save|reallocate)$"),
    ) -> SamplingView:
        """Every pair's stability and the next crawl's plan; `?policy=` simulates."""
        return await run_in_threadpool(runner.sampling_view, project_id, policy)
