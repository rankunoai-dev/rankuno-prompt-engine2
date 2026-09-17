"""FastAPI application: JSON API plus the single-page control-plane UI.

Routes are thin: validation is done by the `StrictModel`s, persistence by
`ProjectStore`, scheduling and execution by `ProjectRunner`, queuing and
progress by `JobManager`. `KeyError` from the store or job manager becomes 404;
`ValueError` (bad interval, bad import) becomes 400.

A run request returns 202 with a `RunJob` immediately; the page polls
`GET /api/jobs/{id}` for progress and completion.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from src.core.config import Settings, get_settings
from src.integrations.schemas import Engine
from src.integrations.usage import get_usage_ledger
from src.modules.control_plane.jobs import JobManager
from src.modules.control_plane.runner import ProjectRunner, engine_options
from src.modules.control_plane.schemas import (
    INTERVAL_PRESETS,
    ActionCard,
    ActionUpdate,
    ConsolidateRequest,
    Consolidation,
    InsightsView,
    PositionsView,
    Project,
    ProjectCreate,
    ProjectRunRecord,
    ProjectUpdate,
    PromptResult,
    RunJob,
    RunRequest,
    TrackedPrompt,
    TrackedPromptCreate,
    TrackedPromptUpdate,
)
from src.modules.control_plane.store import ProjectStore
from src.modules.prompt_tracking.atlas_export import export_atlas
from src.modules.prompt_tracking.costing import CostReport, build_cost_report
from src.modules.prompt_tracking.schemas import AnswerSample
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB

__all__ = ["STATIC_DIR", "create_app"]

STATIC_DIR = Path(__file__).resolve().parent / "static"
_REPO_ROOT = Path(__file__).resolve().parents[3]


class ImportBody(BaseModel):
    """Prompts-file text posted from the browser (files are read client-side)."""

    text: str = Field(min_length=1, max_length=2_000_000)


class RunRow(BaseModel):
    """One row of a project's runs table."""

    run_id: str
    started_at: str
    finished_at: str
    prompts_selected: int
    engine_calls: int
    failed_engine_calls: int
    estimated_cost_usd: float
    report_path: str | None


def create_app(
    store: ProjectStore,
    db: TimeSeriesDB,
    runner: ProjectRunner,
    jobs: JobManager | None = None,
    settings: Callable[[], Settings] | None = None,
) -> FastAPI:
    """Build the application with injected collaborators.

    `settings` supplies the configuration the costing report compares against;
    tests inject a hermetic one.
    """
    app = FastAPI(title="RankUno Prompt Engine - Control Plane", version="0.3.0")
    manager = jobs or JobManager(runner, store)
    store_settings = settings or get_settings

    @app.exception_handler(KeyError)
    async def _not_found(_: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": f"Not found: {exc.args[0]}"})

    @app.exception_handler(ValueError)
    async def _bad_request(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    # -- UI --------------------------------------------------------------------

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/docs/prompt-atlas.html", include_in_schema=False)
    async def atlas_ui() -> FileResponse:
        return FileResponse(_REPO_ROOT / "docs" / "prompt-atlas.html")

    @app.get("/reports/prompt-atlas-data.json", include_in_schema=False)
    @app.get("/docs/prompt-atlas-data.json", include_in_schema=False)
    async def atlas_data(lob: str | None = None) -> JSONResponse:
        """Prompt Atlas dataset built live from the tracker store (never stale)."""
        projects = store.list_projects()
        domains = sorted({d for p in projects for d in p.client.domains})
        competitors = sorted({d for p in projects for d in p.client.competitor_domains})
        try:
            document = export_atlas(db.path, domains=domains, competitors=competitors, lob=lob)
        except FileNotFoundError:
            return JSONResponse(status_code=404, content={"detail": "No tracker database yet."})
        return JSONResponse(document)

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        return {"status": "ok", "active_jobs": len(manager.active())}

    @app.get("/api/options")
    async def options() -> dict[str, object]:
        return {
            "engines": engine_options(),
            "intervals": [{"value": v, "label": label} for v, label in INTERVAL_PRESETS],
            "models": {
                "CHATGPT_SEARCH": [
                    {"value": "gpt-4o-mini", "label": "GPT-4o Mini (Fast & Low Cost)"},
                    {"value": "gpt-4o", "label": "GPT-4o (Full Web Search Model)"},
                    {"value": "gpt-4.5-preview", "label": "GPT-4.5 Preview"},
                    {"value": "o3-mini", "label": "o3-Mini Reasoning"},
                ],
                "PERPLEXITY": [
                    {"value": "sonar-pro", "label": "Perplexity Sonar Pro"},
                    {"value": "sonar", "label": "Perplexity Sonar"},
                    {"value": "sonar-reasoning", "label": "Perplexity Sonar Reasoning"},
                    {
                        "value": "google/gemini-3.6-flash",
                        "label": "Google Gemini 3.6 Flash (Sonar API)",
                    },
                ],
                "GEMINI": [
                    {"value": "gemini-3.6-flash", "label": "Gemini 3.6 Flash"},
                    {"value": "gemini-1.5-pro", "label": "Gemini 1.5 Pro"},
                    {"value": "gemini-2.0-flash", "label": "Gemini 2.0 Flash"},
                ],
                "GOOGLE_AI_OVERVIEW": [
                    {"value": "default-desktop", "label": "Google SERP Desktop"},
                    {"value": "google-mobile", "label": "Google SERP Mobile"},
                ],
            },
        }

    # -- projects ----------------------------------------------------------------

    @app.get("/api/projects", response_model=list[Project])
    async def list_projects() -> list[Project]:
        return store.list_projects()

    @app.post("/api/projects", response_model=Project, status_code=201)
    async def create_project(body: ProjectCreate) -> Project:
        return store.create_project(body)

    @app.get("/api/projects/{project_id}", response_model=Project)
    async def get_project(project_id: str) -> Project:
        return store.get_project(project_id)

    @app.put("/api/projects/{project_id}", response_model=Project)
    async def update_project(project_id: str, body: ProjectUpdate) -> Project:
        return store.update_project(project_id, body)

    @app.delete("/api/projects/{project_id}", status_code=204)
    async def delete_project(project_id: str) -> Response:
        store.delete_project(project_id)
        return Response(status_code=204)

    # -- prompts -----------------------------------------------------------------

    @app.get("/api/projects/{project_id}/prompts", response_model=list[TrackedPrompt])
    async def list_prompts(project_id: str) -> list[TrackedPrompt]:
        store.get_project(project_id)
        return store.list_prompts(project_id)

    @app.post("/api/projects/{project_id}/prompts", response_model=TrackedPrompt, status_code=201)
    async def add_prompt(project_id: str, body: TrackedPromptCreate) -> TrackedPrompt:
        return store.add_prompt(project_id, body)

    @app.post("/api/projects/{project_id}/prompts/import", response_model=list[TrackedPrompt])
    async def import_prompts(project_id: str, body: ImportBody) -> list[TrackedPrompt]:
        store.get_project(project_id)
        return store.import_prompts(project_id, body.text)

    @app.put("/api/projects/{project_id}/prompts/{tracked_id}", response_model=TrackedPrompt)
    async def update_prompt(
        project_id: str, tracked_id: str, body: TrackedPromptUpdate
    ) -> TrackedPrompt:
        return store.update_prompt(project_id, tracked_id, body)

    @app.delete("/api/projects/{project_id}/prompts/{tracked_id}", status_code=204)
    async def delete_prompt(project_id: str, tracked_id: str) -> Response:
        store.delete_prompt(project_id, tracked_id)
        return Response(status_code=204)

    # -- runs, jobs & results ----------------------------------------------------

    @app.get("/api/projects/{project_id}/results", response_model=list[PromptResult])
    async def results(project_id: str) -> list[PromptResult]:
        return runner.results(project_id)

    @app.post("/api/projects/{project_id}/run", response_model=RunJob, status_code=202)
    async def run(project_id: str, body: RunRequest | None = None) -> RunJob:
        return manager.submit(project_id, body or RunRequest())

    @app.get("/api/projects/{project_id}/jobs", response_model=list[RunJob])
    async def project_jobs(project_id: str, limit: int = 20) -> list[RunJob]:
        store.get_project(project_id)
        return manager.list_jobs(project_id, limit=max(1, min(limit, 200)))

    @app.get("/api/jobs", response_model=list[RunJob])
    async def all_jobs(active: bool = False, limit: int = 50) -> list[RunJob]:
        return manager.active() if active else manager.list_jobs(limit=max(1, min(limit, 200)))

    @app.get("/api/jobs/{job_id}", response_model=RunJob)
    async def get_job(job_id: str) -> RunJob:
        return manager.get(job_id)

    @app.post("/api/projects/{project_id}/consolidate", response_model=Consolidation)
    async def consolidate(project_id: str, body: ConsolidateRequest | None = None) -> Consolidation:
        """Recompute the project's positions now, over its window (or a custom one)."""
        return runner.consolidate(project_id, body or ConsolidateRequest())

    @app.get("/api/projects/{project_id}/positions", response_model=PositionsView)
    async def positions(project_id: str, consolidation_id: str | None = None) -> PositionsView:
        return runner.positions(project_id, consolidation_id)

    @app.get("/api/projects/{project_id}/crawls", response_model=list[ProjectRunRecord])
    async def crawls(project_id: str) -> list[ProjectRunRecord]:
        return runner.crawls(project_id)

    @app.get("/api/projects/{project_id}/insights", response_model=InsightsView)
    async def insights(project_id: str, consolidation_id: str | None = None) -> InsightsView:
        """Verdicts, what changed, action cards, fan-out, claims, trust, pages."""
        return runner.insights(project_id, consolidation_id)

    @app.put("/api/projects/{project_id}/actions/{action_id}", response_model=ActionCard)
    async def update_action(project_id: str, action_id: str, body: ActionUpdate) -> ActionCard:
        return runner.update_action(project_id, action_id, body)

    @app.get("/api/projects/{project_id}/samples", response_model=list[AnswerSample])
    async def samples(
        project_id: str, prompt_id: str, engine: Engine | None = None, run_id: str | None = None
    ) -> list[AnswerSample]:
        """Raw answer samples with full text, the engine's queries, claims and snippets."""
        return runner.samples(project_id, prompt_id, engine, run_id)

    @app.get("/api/costs", response_model=CostReport)
    async def costs(project_id: str | None = None, days: int | None = None) -> CostReport:
        """Spend and volume per vendor; `project_id` narrows to that client's runs."""
        run_ids: list[str] | None = None
        if project_id is not None:
            project = store.get_project(project_id)
            run_ids = [str(r["run_id"]) for r in db.runs_for(project.client.lob, limit=1000)]
        active = store_settings()
        return build_cost_report(get_usage_ledger(active), active, days=days, run_ids=run_ids)

    @app.get("/api/projects/{project_id}/runs", response_model=list[RunRow])
    async def runs(project_id: str) -> list[RunRow]:
        project = store.get_project(project_id)
        return [RunRow.model_validate(row) for row in db.runs_for(project.client.lob, limit=50)]

    @app.get("/api/projects/{project_id}/export")
    async def export(project_id: str) -> JSONResponse:
        project = store.get_project(project_id)
        prompts = store.list_prompts(project_id)
        return JSONResponse(
            {
                "project": project.model_dump(mode="json"),
                "prompts": [p.model_dump(mode="json") for p in prompts],
            }
        )

    return app
