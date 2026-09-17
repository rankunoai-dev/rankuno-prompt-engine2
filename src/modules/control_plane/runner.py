"""Runs a project's due work through the tracker pipeline and reads results back.

Every batch is one `PromptTrackerPipeline.run()` with the project's client
profile, the batch's platforms and the batch's prompts as `custom_prompts`, so
governance (approval, budget, call cap, reuse, breakers) is exactly what the CLI
gets. Nothing here talks to a vendor directly.

Progress: the pipeline reports per-batch events (`PipelineProgress`); the runner
folds them into one run-level `RunProgress` (checks across all batches, batch
count, paid engine calls) and hands each update to the caller's sink.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel

from src.core.config import Settings, get_settings
from src.core.guardrails import GuardrailEngine
from src.core.logger import get_logger
from src.core.rate_limiter import CostLedger
from src.core.schemas import ToolResult
from src.integrations.schemas import Engine
from src.modules.control_plane.actions import ActionStateStore
from src.modules.control_plane.insights import InsightEngine
from src.modules.control_plane.planner import batches, due_items, effective_engines
from src.modules.control_plane.positioning import PositionStore
from src.modules.control_plane.schemas import (
    ActionCard,
    ActionUpdate,
    ConsolidateRequest,
    Consolidation,
    InsightsView,
    PositionsView,
    Project,
    ProjectRunRecord,
    PromptResult,
    RunOutcome,
    RunProgress,
    RunRequest,
    TrackedPrompt,
    WorkBatch,
)
from src.modules.control_plane.store import ProjectStore
from src.modules.prompt_tracking.pipeline import ProgressCallback, PromptTrackerPipeline
from src.modules.prompt_tracking.scheduler import parse_interval
from src.modules.prompt_tracking.schemas import (
    AnswerSample,
    CitationSnapshot,
    CustomPrompt,
    PipelineInput,
    PipelinePhase,
    PipelineProgress,
    RankQueryKind,
)
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB

__all__ = ["PipelineRunner", "ProgressSink", "ProjectRunner", "engine_options"]

_logger = get_logger("modules.control_plane.runner")

PipelineRunner = Callable[[PipelineInput, ProgressCallback | None], ToolResult[BaseModel]]
"""Executes one pipeline input, reporting to the optional callback. Injected in tests."""

ProgressSink = Callable[[RunProgress], None]
"""Receives run-level progress updates (UI job manager, logger, …)."""

_GENERATE_JOB = "project:{project_id}:generate"
_ENGINE_LABELS = {
    Engine.GOOGLE_AI_OVERVIEW: "Google AI Overview",
    Engine.CHATGPT_SEARCH: "ChatGPT Search",
    Engine.PERPLEXITY: "Perplexity",
    Engine.GEMINI: "Gemini",
}


def engine_options() -> list[dict[str, str]]:
    """Platforms the UI can offer, with labels."""
    return [{"value": e.value, "label": _ENGINE_LABELS[e]} for e in Engine]


class _ProgressTracker:
    """Folds per-batch pipeline events into one run-level progress stream."""

    def __init__(self, sink: ProgressSink | None, *, batches_total: int, checks_total: int) -> None:
        self._sink = sink
        self.batches_total = batches_total
        self.checks_total = checks_total
        self.batches_done = 0
        self._checks_before = 0  # completed in earlier batches
        self._calls_before = 0
        self._current_total = 0
        self._current_done = 0
        self._current_calls = 0

    def emit(self, phase: str, message: str) -> None:
        """Publish the current totals under `phase`."""
        if self._sink is None:
            return
        done = self._checks_before + self._current_done
        percent = 100.0 * done / self.checks_total if self.checks_total else 0.0
        if phase != PipelinePhase.DONE.value:  # later batches may still grow the total
            percent = min(percent, 99.0)
        self._sink(
            RunProgress(
                phase=phase,
                message=message,
                checks_done=done,
                checks_total=self.checks_total,
                batches_done=self.batches_done,
                batches_total=self.batches_total,
                engine_calls=self._calls_before + self._current_calls,
                percent=round(min(percent, 100.0), 1),
            )
        )

    def start_batch(self, expected_checks: int, message: str) -> None:
        """Announce a batch whose check count is known (or 0 if not yet)."""
        self._current_total, self._current_done, self._current_calls = expected_checks, 0, 0
        self.emit(PipelinePhase.AUDIT.value, message)

    def on_event(self, event: PipelineProgress) -> None:
        """Pipeline callback: grow the plan if the batch turned out bigger than expected."""
        if event.total > self._current_total:  # generation batches are unknown up front
            self.checks_total += event.total - self._current_total
            self._current_total = event.total
        if event.phase is not PipelinePhase.HARVEST:
            self._current_done = min(event.done, self._current_total)
        self._current_calls = event.calls
        self.emit(event.phase.value, event.message)

    def finish_batch(self) -> None:
        """Close the batch: everything planned for it counts as done."""
        self._checks_before += self._current_total
        self._calls_before += self._current_calls
        self._current_total = self._current_done = self._current_calls = 0
        self.batches_done += 1


class ProjectRunner:
    """Turns project configuration into pipeline runs."""

    def __init__(
        self,
        store: ProjectStore,
        db: TimeSeriesDB,
        *,
        settings: Settings | None = None,
        guardrails: GuardrailEngine | None = None,
        ledger: CostLedger | None = None,
        pipeline: PipelineRunner | None = None,
        clock: Callable[[], datetime] | None = None,
        positions: PositionStore | None = None,
    ) -> None:
        """Build a runner.

        Args:
            store: Project/prompt configuration.
            db: Time-series store (history, reuse, job state).
            settings: Configuration override.
            guardrails: Approval engine for the pipeline (deny-by-default if None).
            ledger: Spend ledger shared across batches.
            pipeline: Executes a `PipelineInput`; defaults to the real tool.
            clock: UTC time source.
            positions: Crawl history and consolidated positions store (defaults to the
                tracker database).
        """
        self._store = store
        self._db = db
        self._settings = settings or get_settings()
        self._guardrails = guardrails
        self._ledger = ledger or CostLedger()
        self._pipeline: PipelineRunner = pipeline or self._default_pipeline
        self._clock = clock or (lambda: datetime.now(UTC))
        self._positions = positions or PositionStore(db.path)
        self._insights = InsightEngine(db, self._positions, ActionStateStore(db.path))

    def _default_pipeline(
        self, payload: PipelineInput, progress: ProgressCallback | None
    ) -> ToolResult[BaseModel]:
        tool = PromptTrackerPipeline(
            self._guardrails,
            self._ledger,
            settings=self._settings,
            db=self._db,
            progress=progress,
        )
        return tool.run(payload)

    # -- running -------------------------------------------------------------

    def run(
        self,
        project_id: str,
        request: RunRequest | None = None,
        progress: ProgressSink | None = None,
    ) -> RunOutcome:
        """Run what is due for one project (or what `request` selects).

        `progress`, when given, receives a `RunProgress` for every pipeline event.
        """
        request = request or RunRequest()
        project = self._store.get_project(project_id)
        prompts = self._store.list_prompts(project_id)
        now = self._clock()
        items = due_items(
            project,
            prompts,
            self._db,
            now,
            force=request.force,
            only_ids=request.prompt_ids or None,
            engines_filter=request.engines,
        )
        plan = batches(project, items)
        generate = (
            project.generate_prompts
            and not request.prompt_ids
            and self._generation_due(project, now, request.force)
        )
        tracker = _ProgressTracker(
            progress,
            batches_total=len(plan) + int(generate),
            checks_total=sum(len(b.prompts) * len(b.engines) for b in plan),
        )
        tracker.emit("planning", f"{len(plan)} batch(es) planned")
        outcome = RunOutcome(project_id=project_id, started_at=now, batches=0, prompts_run=0)
        for batch in plan:
            self._run_batch(project, batch, outcome, tracker)
        if generate:
            self._run_generation(project, now, outcome, tracker)
        if outcome.batches == 0:
            outcome.reason = "nothing due"
        else:
            self._record_crawl(project, prompts, request, outcome, tracker)
        tracker.emit(PipelinePhase.DONE.value, outcome.reason or "Run finished")
        return outcome

    def _record_crawl(
        self,
        project: Project,
        prompts: list[TrackedPrompt],
        request: RunRequest,
        outcome: RunOutcome,
        tracker: _ProgressTracker,
    ) -> None:
        """Store the crawl; consolidate positions when the window is complete."""
        record = ProjectRunRecord(
            id=uuid.uuid4().hex[:16],
            project_id=project.id,
            started_at=outcome.started_at,
            finished_at=self._clock(),
            run_ids=list(outcome.run_ids),
            prompts_run=outcome.prompts_run,
            batches=outcome.batches,
            statuses=list(outcome.statuses),
            full=not request.prompt_ids,
        )
        self._positions.record_project_run(record)
        outcome.project_run_id = record.id
        if not record.full:
            return
        pending = self._positions.runs_since_last_consolidation(project.id)
        if pending >= project.consolidation_runs:
            tracker.emit("consolidating", f"Consolidating positions over {pending} crawl(s)")
            consolidation = self._positions.consolidate(
                project,
                prompts,
                window_runs=project.consolidation_runs,
                trigger="auto",
                now=self._clock(),
            )
            outcome.consolidation_id = consolidation.id

    def consolidate(self, project_id: str, request: ConsolidateRequest) -> Consolidation:
        """Analyst-triggered consolidation over the project's (or a custom) window."""
        project = self._store.get_project(project_id)
        return self._positions.consolidate(
            project,
            self._store.list_prompts(project_id),
            window_runs=request.window_runs or project.consolidation_runs,
            trigger="manual",
            note=request.note,
            now=self._clock(),
        )

    def positions(self, project_id: str, consolidation_id: str | None = None) -> PositionsView:
        """Latest (or chosen) consolidated positions for a project."""
        self._store.get_project(project_id)
        return self._positions.positions(project_id, consolidation_id)

    def insights(self, project_id: str, consolidation_id: str | None = None) -> InsightsView:
        """Verdicts, changes, action cards and raw-material maps for a project."""
        project = self._store.get_project(project_id)
        return self._insights.build(
            project,
            self._store.list_prompts(project_id),
            consolidation_id=consolidation_id,
            now=self._clock(),
        )

    def update_action(self, project_id: str, action_id: str, update: ActionUpdate) -> ActionCard:
        """Record analyst state on an action card."""
        project = self._store.get_project(project_id)
        return self._insights.update_action(
            project, self._store.list_prompts(project_id), action_id, update, now=self._clock()
        )

    def samples(
        self, project_id: str, prompt_id: str, engine: Engine | None, run_id: str | None
    ) -> list[AnswerSample]:
        """Raw answer samples (full text, queries, claims, snippets) for one prompt."""
        self._store.get_project(project_id)
        return self._db.samples_for(
            prompt_id, engine=engine, run_ids=[run_id] if run_id else None, limit=200
        )

    def crawls(self, project_id: str) -> list[ProjectRunRecord]:
        """Crawl history for a project, newest first."""
        self._store.get_project(project_id)
        return self._positions.project_runs(project_id)

    def run_due_all(self) -> list[RunOutcome]:
        """Run due work for every enabled project, synchronously."""
        return [self.run(p.id) for p in self._store.list_projects() if p.enabled]

    def _record(self, result: ToolResult[BaseModel], outcome: RunOutcome, label: str) -> None:
        outcome.batches += 1
        outcome.statuses.append(result.status.value)
        run_id = getattr(result.data, "run_id", None) if result.data is not None else None
        if run_id:
            outcome.run_ids.append(str(run_id))
        if result.data is not None:
            outcome.warnings.extend(getattr(result.data, "warnings", []))
        if result.error:
            outcome.warnings.append(f"{label} {result.status.value}: {result.error}")

    def _run_batch(
        self, project: Project, batch: WorkBatch, outcome: RunOutcome, tracker: _ProgressTracker
    ) -> None:
        payload = PipelineInput(
            client=project.client,
            engines=batch.engines,
            samples_per_engine=batch.samples_per_engine,
            resolve_redirects=project.resolve_redirects,
            track_keyword_rank=project.track_keyword_rank,
            custom_prompts=[
                CustomPrompt(prompt_text=p.prompt_text, keyword=p.keyword, subtopic=p.subtopic)
                for p in batch.prompts
            ],
            generate_prompts=False,
            max_engine_calls=project.max_engine_calls,
            reuse_within_hours=project.reuse_within_hours,
        )
        tracker.start_batch(
            len(batch.prompts) * len(batch.engines),
            f"Batch {tracker.batches_done + 1}/{tracker.batches_total}: "
            f"{len(batch.prompts)} prompt(s) on {len(batch.engines)} platform(s)",
        )
        if project.engine_models:
            old_settings = self._settings
            new_settings = self._settings.model_copy()
            if Engine.CHATGPT_SEARCH in project.engine_models:
                new_settings.openai_search_model = project.engine_models[Engine.CHATGPT_SEARCH]
            if Engine.PERPLEXITY in project.engine_models:
                new_settings.perplexity_model = project.engine_models[Engine.PERPLEXITY]
            if Engine.GEMINI in project.engine_models:
                new_settings.gemini_model = project.engine_models[Engine.GEMINI]
            self._settings = new_settings
            try:
                result = self._pipeline(payload, tracker.on_event)
            finally:
                self._settings = old_settings
        else:
            result = self._pipeline(payload, tracker.on_event)
        tracker.finish_batch()
        outcome.prompts_run += len(batch.prompts)
        self._record(result, outcome, "batch")
        _logger.info(
            "project_batch_finished",
            extra={
                "project_id": project.id,
                "engines": [e.value for e in batch.engines],
                "prompts": len(batch.prompts),
                "status": result.status.value,
            },
        )

    def _generation_due(self, project: Project, now: datetime, force: bool) -> bool:
        """True when the Semrush-generated set should run at the project interval."""
        state = self._db.job_state(_GENERATE_JOB.format(project_id=project.id)) or {}
        last = state.get("last_run_at")
        if force or not last:
            return True
        return datetime.fromisoformat(last) <= now - parse_interval(project.interval)

    def _run_generation(
        self, project: Project, now: datetime, outcome: RunOutcome, tracker: _ProgressTracker
    ) -> None:
        """Run the Semrush-generated set and record the cadence state."""
        name = _GENERATE_JOB.format(project_id=project.id)
        interval = parse_interval(project.interval)
        payload = PipelineInput(
            client=project.client,
            engines=project.engines,
            samples_per_engine=project.samples_per_engine,
            resolve_redirects=project.resolve_redirects,
            track_keyword_rank=project.track_keyword_rank,
            generate_prompts=True,
            max_engine_calls=project.max_engine_calls,
            reuse_within_hours=project.reuse_within_hours,
        )
        tracker.start_batch(0, "Semrush-generated prompt set")
        result = self._pipeline(payload, tracker.on_event)
        tracker.finish_batch()
        run_id = getattr(result.data, "run_id", None) if result.data is not None else None
        self._db.record_job_run(
            name,
            interval=project.interval,
            last_run_at=now,
            last_run_id=str(run_id) if run_id else None,
            last_status=result.status.value,
            next_run_at=now + interval,
        )
        self._record(result, outcome, "generation")

    # -- reading -------------------------------------------------------------

    def results(self, project_id: str) -> list[PromptResult]:
        """Latest snapshot per prompt per platform, plus what is due now."""
        project = self._store.get_project(project_id)
        prompts = self._store.list_prompts(project_id)
        now = self._clock()
        due = {item.prompt.id: item.engines for item in due_items(project, prompts, self._db, now)}
        out: list[PromptResult] = []
        for prompt in prompts:
            engines = effective_engines(project, prompt)
            snapshots: dict[str, CitationSnapshot | None] = {}
            for engine in engines:
                history = self._db.history(prompt.prompt_id, engine, limit=1)
                snapshots[engine.value] = history[0] if history else None
            organic_prompt = self._db.organic_history(
                prompt.prompt_id, RankQueryKind.PROMPT, limit=1
            )
            organic_keyword = self._db.organic_history(
                prompt.prompt_id, RankQueryKind.KEYWORD, limit=1
            )
            out.append(
                PromptResult(
                    prompt=prompt,
                    effective_interval=prompt.interval or project.interval,
                    effective_engines=engines,
                    snapshots=snapshots,
                    organic_prompt=organic_prompt[0] if organic_prompt else None,
                    organic_keyword=organic_keyword[0] if organic_keyword else None,
                    due_on=due.get(prompt.id, []),
                )
            )
        return out
