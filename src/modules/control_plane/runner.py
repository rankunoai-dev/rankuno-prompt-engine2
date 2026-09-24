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
from src.integrations.anthropic_judge import AnthropicJudgeClient
from src.integrations.schemas import Engine
from src.modules.alerting.dispatch import AlertDispatcher
from src.modules.alerting.store import AlertStore
from src.modules.alerting.triggers import evaluate
from src.modules.control_plane import crawler_cards
from src.modules.control_plane.actions import ActionStateStore
from src.modules.control_plane.insights import InsightEngine
from src.modules.control_plane.planner import batches, effective_engines, effective_interval
from src.modules.control_plane.positioning import PositionStore
from src.modules.control_plane.sampling import FIXED
from src.modules.control_plane.sampling import plan as sampling_plan
from src.modules.control_plane.schemas import (
    ActionCard,
    ActionUpdate,
    ConsolidateRequest,
    Consolidation,
    EngineStatus,
    InsightsView,
    PositionsView,
    Project,
    ProjectRunRecord,
    PromptCapture,
    PromptDetail,
    PromptEngineDetail,
    PromptResult,
    RunOutcome,
    RunProgress,
    RunRequest,
    SamplingView,
    TrackedPrompt,
    WorkBatch,
)
from src.modules.control_plane.store import ProjectStore
from src.modules.crawler_logs.store import CrawlerLogStore
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
from src.modules.prompt_tracking.sentiment import SentimentJudge, judge_samples
from src.modules.prompt_tracking.stability import classify
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
_SAMPLES_LIMIT = 200
_SAMPLES_MAX = 1000
_HISTORY_LIMIT = 100


def _engines_with_history(
    configured: list[Engine], db: TimeSeriesDB, prompt_id: str
) -> list[Engine]:
    """Configured platforms, plus any dropped one that still holds history.

    A project that stops tracking a platform keeps the crawls it already paid for;
    hiding them would look like the data never existed.
    """
    extra = [e for e in Engine if e not in configured and db.history(prompt_id, e, limit=1)]
    return [*configured, *extra]


def _engine_detail(
    engine: Engine, configured: list[Engine], db: TimeSeriesDB, prompt_id: str
) -> PromptEngineDetail:
    """One platform's standing, distinguishing the three causes of an empty cell."""
    history = db.history(prompt_id, engine, limit=_HISTORY_LIMIT)
    samples = sum(s.samples for s in history)
    failed = sum(s.failed_samples for s in history)
    cited_samples = sum(s.client_cited_samples for s in history)
    if engine not in configured:
        status = EngineStatus.NOT_CONFIGURED
    elif not history:
        status = EngineStatus.NEVER_ASKED
    elif samples - failed <= 0:
        status = EngineStatus.ASKED_FAILED
    else:
        status = EngineStatus.HAS_DATA
    latest = history[0] if history else None
    return PromptEngineDetail(
        engine=engine,
        status=status,
        crawls=len(history),
        samples=samples,
        ok_samples=max(samples - failed, 0),
        failed_samples=failed,
        cited_samples=cited_samples,
        # Stored, never recomputed: the written denominator excludes failed samples
        # and the value is rounded at write time.
        citation_rate=latest.client_citation_rate if latest else None,
        mention_rate=latest.mention_rate if latest else None,
        best_rank=latest.client_best_rank if latest else None,
        cited=bool(latest and latest.client_cited),
        cited_in_minority=bool(
            latest and not latest.client_cited and latest.client_cited_samples > 0
        ),
        models=list(dict.fromkeys(s.model for s in history if s.model)),
        history=history,
        velocity=db.velocity(prompt_id, engine),
    )


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
        judge: SentimentJudge | None = None,
        dispatcher: AlertDispatcher | None = None,
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
            judge: Sentiment judge (ADR 0021); defaults to the Anthropic client when a
                key is configured, else judging is skipped.
            dispatcher: Outbound alert dispatcher (ADR 0024); the default reads the
                project's own destination and sends nothing when none is configured.
        """
        self._store = store
        self._db = db
        self._settings = settings or get_settings()
        self._judge = judge
        self._guardrails = guardrails
        self._ledger = ledger or CostLedger()
        self._pipeline: PipelineRunner = pipeline or self._default_pipeline
        self._clock = clock or (lambda: datetime.now(UTC))
        self._positions = positions or PositionStore(db.path)
        self._crawler_logs = CrawlerLogStore(db.path)
        self._alerts = AlertStore(db.path)
        self._dispatcher = dispatcher or AlertDispatcher(self._alerts, settings=self._settings)
        self._insights = InsightEngine(
            db,
            self._positions,
            ActionStateStore(db.path),
            extra_cards=lambda project, prompts: crawler_cards.cards_for(
                project, prompts, self._crawler_logs, db
            ),
        )

    @property
    def crawler_logs(self) -> CrawlerLogStore:
        """The crawler-log store sharing the tracker database (ADR 0022)."""
        return self._crawler_logs

    @property
    def alerts(self) -> AlertStore:
        """The alert destinations and history, in the tracker database (ADR 0024)."""
        return self._alerts

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
        # One plan decides due-ness, stretches and boosts for the whole crawl (ADR 0025).
        sampling = sampling_plan(
            project, prompts, self._db, self._positions, self._settings, now, request=request
        )
        plan = batches(project, sampling.items)
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
        outcome = RunOutcome(
            project_id=project_id,
            started_at=now,
            batches=0,
            prompts_run=0,
            sampling=sampling.summary,
        )
        for batch in plan:
            self._run_batch(project, batch, outcome, tracker)
        if generate:
            self._run_generation(project, now, outcome, tracker)
        if outcome.batches == 0:
            outcome.reason = "nothing due"
            summary = sampling.summary
            if summary.skipped_pairs and summary.next_due_at is not None:
                outcome.reason += (
                    f"; {summary.skipped_pairs} stable pair(s) stretched, next due "
                    f"{summary.next_due_at.date()}"
                )
        else:
            self._judge_mentions(project, outcome, tracker)
            self._record_crawl(project, prompts, request, outcome, tracker)
            self._raise_alerts(project, prompts, outcome, tracker)
        tracker.emit(PipelinePhase.DONE.value, outcome.reason or "Run finished")
        return outcome

    def _raise_alerts(
        self,
        project: Project,
        prompts: list[TrackedPrompt],
        outcome: RunOutcome,
        tracker: _ProgressTracker,
    ) -> None:
        """Evaluate the alert rules for the window this crawl just closed.

        Never raises. A crawl that succeeded must not be reported as failed
        because Slack was down, and an alerting bug must not cost the run.
        """
        try:
            destination = self._alerts.destination(project.id)
            if not destination.enabled or not destination.rules:
                return  # no destination, no work: an idle project costs nothing
            positions = self._positions.positions(project.id)
            history = positions.history
            previous = (
                self._positions.positions(project.id, history[1].id).positions
                if len(history) > 1
                else []
            )
            if not previous:
                return  # nothing to compare against yet; every rule is a delta
            insights = self.insights(project.id)
            events = evaluate(
                insights,
                positions.positions,
                previous,
                rules=set(destination.rules),
                important_prompt_ids={p.prompt_id for p in prompts if p.important},
            )
            if not events:
                return
            tracker.emit("alerting", f"{len(events)} alert(s) to consider")
            records = self._dispatcher.dispatch(project.id, project.name, events)
            sent = [record for record in records if record.delivered]
            if sent:
                outcome.warnings.append(f"{len(sent)} alert(s) sent")
        except Exception as error:  # noqa: BLE001 - alerting never fails a crawl
            _logger.warning(
                "alerting_failed",
                extra={"project_id": project.id, "error": f"{type(error).__name__}: {error}"},
            )
            outcome.warnings.append(f"Alerting failed: {type(error).__name__}")

    def _resolve_judge(self) -> SentimentJudge | None:
        """The configured judge, built once; None when there is no key or the cap is zero."""
        if self._judge is not None:
            return self._judge
        if self._settings.anthropic_api_key is None:
            return None
        if not self._settings.anthropic_api_key.get_secret_value():
            return None
        if self._settings.sentiment_max_sentences_per_run <= 0:
            return None
        self._judge = AnthropicJudgeClient(self._settings)
        return self._judge

    def _judge_mentions(
        self, project: Project, outcome: RunOutcome, tracker: _ProgressTracker
    ) -> None:
        """Score the crawl's mention sentences (ADR 0021). Never fails the crawl."""
        if not project.sentiment:
            return
        judge = self._resolve_judge()
        if judge is None:
            _logger.info("judge_skipped", extra={"project_id": project.id, "reason": "no key"})
            return
        samples: list[AnswerSample] = []
        for pid in {p.prompt_id for p in self._store.list_prompts(project.id)}:
            samples.extend(self._db.samples_for(pid, run_ids=list(outcome.run_ids)))
        if not any(s.mentions for s in samples):
            return
        tracker.emit("judging", "Scoring brand mentions")
        try:
            summary = judge_samples(
                self._db,
                judge,
                samples,
                project.client,
                self._settings,
                run_id=outcome.run_ids[0] if outcome.run_ids else None,
                progress=lambda done, total: tracker.emit(
                    "judging", f"Scored {done} of {total} sentences"
                ),
                now=self._clock,
            )
        except Exception:  # noqa: BLE001 - the crawl's data is stored; judging is best effort
            _logger.exception("judge_step_failed", extra={"project_id": project.id})
            outcome.warnings.append("Sentiment scoring failed; mentions are unscored.")
            return
        if summary.unscored or summary.refused:
            outcome.warnings.append(
                f"Sentiment: {summary.scored + summary.cached} scored, "
                f"{summary.unscored} unscored, {summary.refused} refused."
            )

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
            # A run restricted to some prompts or some platforms is not a full crawl and
            # must not advance the consolidation window.
            full=not request.prompt_ids and not request.engines,
            sampling=outcome.sampling,
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

    def insights(
        self,
        project_id: str,
        consolidation_id: str | None = None,
        *,
        prompt_id: str | None = None,
    ) -> InsightsView:
        """Verdicts, changes, action cards and raw-material maps for a project.

        `prompt_id` scopes the whole view to one prompt. The engine raises
        `KeyError` for an id the project does not track, which the app maps to 404.
        """
        project = self._store.get_project(project_id)
        return self._insights.build(
            project,
            self._store.list_prompts(project_id),
            consolidation_id=consolidation_id,
            prompt_id=prompt_id,
            now=self._clock(),
        )

    def update_action(self, project_id: str, action_id: str, update: ActionUpdate) -> ActionCard:
        """Record analyst state on an action card."""
        project = self._store.get_project(project_id)
        return self._insights.update_action(
            project, self._store.list_prompts(project_id), action_id, update, now=self._clock()
        )

    def samples(
        self,
        project_id: str,
        prompt_id: str,
        engine: Engine | None,
        run_id: str | None,
        *,
        limit: int = _SAMPLES_LIMIT,
    ) -> list[AnswerSample]:
        """Raw answer samples (full text, queries, claims, snippets) for one prompt."""
        self._store.get_project(project_id)
        if not any(p.prompt_id == prompt_id for p in self._store.list_prompts(project_id)):
            # History is keyed by (lob, text) alone, so without this any project id
            # would serve any prompt's samples.
            raise KeyError(prompt_id)
        return self._db.samples_for(
            prompt_id,
            engine=engine,
            run_ids=[run_id] if run_id else None,
            limit=max(1, min(limit, _SAMPLES_MAX)),
        )

    def prompt_detail(self, project_id: str, tracked_id: str) -> PromptDetail:
        """Everything known about one prompt: per-engine series, organic, positions.

        Insights are not assembled here: ask `insights(project_id, prompt_id=...)`,
        which scopes the engine before its project-wide caps. Filtering the unscoped
        view on the client loses rows for prompts outside the top-N (ADR 0017).
        """
        project = self._store.get_project(project_id)
        prompt = self._store.get_prompt(project_id, tracked_id)
        pid = prompt.prompt_id
        configured = effective_engines(project, prompt)

        details: list[PromptEngineDetail] = []
        now = self._clock()
        window = self._settings.stability_window_crawls
        for engine in _engines_with_history(configured, self._db, pid):
            detail = _engine_detail(engine, configured, self._db, pid)
            stability = classify(
                detail.history,
                window=window,
                min_crawls=self._settings.stability_min_crawls,
                now=now,
                max_age=effective_interval(project, prompt) * window,
            )
            details.append(detail.model_copy(update={"stability": stability}))

        record = self._db.prompt_records([pid]).get(pid, {})
        organic_prompt = self._db.organic_history(pid, RankQueryKind.PROMPT, limit=_HISTORY_LIMIT)
        organic_keyword = self._db.organic_history(pid, RankQueryKind.KEYWORD, limit=_HISTORY_LIMIT)
        return PromptDetail(
            project_id=project_id,
            lob=project.client.lob,
            result=self._result_for(project, prompt),
            engines=details,
            organic_prompt=organic_prompt,
            organic_keyword=organic_keyword,
            organic_prompt_velocity=self._db.organic_velocity(pid, RankQueryKind.PROMPT),
            organic_keyword_velocity=self._db.organic_velocity(pid, RankQueryKind.KEYWORD),
            run_ids=self._db.sample_run_ids(pid),
            positions=self._positions.positions_for_prompt(project_id, pid),
            content_gap=record.get("mapped_url") is None,
            capture=PromptCapture.model_validate(self._db.capture_coverage(pid)),
            shared_lob_projects=[
                p.name
                for p in self._store.list_projects()
                if p.id != project_id and p.client.lob == project.client.lob
            ],
        )

    def crawls(self, project_id: str) -> list[ProjectRunRecord]:
        """Crawl history for a project, newest first."""
        self._store.get_project(project_id)
        return self._positions.project_runs(project_id)

    def sampling_view(self, project_id: str, policy: str | None = None) -> SamplingView:
        """Dry run of the next crawl under the project's policy, or a simulated one."""
        project = self._store.get_project(project_id)
        prompts = self._store.list_prompts(project_id)
        now = self._clock()
        s = self._settings
        result = sampling_plan(
            project, prompts, self._db, self._positions, s, now, policy=policy, classify_all=True
        )
        warnings: list[str] = []
        reuse_hours = (
            project.reuse_within_hours
            if project.reuse_within_hours is not None
            else s.reuse_within_hours
        )
        if reuse_hours and reuse_hours * 3600 >= parse_interval(project.interval).total_seconds():
            warnings.append(
                f"Snapshots are reused for {reuse_hours} h, which is at least the project "
                "interval: pairs never get fresh answers and their stability cannot move."
            )
        if project.sampling_policy == FIXED and (policy or FIXED) == FIXED:
            warnings.append(
                "The project's policy is fixed. Verdicts are shown for information; add "
                "?policy=save or ?policy=reallocate to see what each would change."
            )
        active = policy or project.sampling_policy
        return SamplingView(
            project_id=project_id,
            policy=active,
            simulated=policy is not None and policy != project.sampling_policy,
            window_crawls=s.stability_window_crawls,
            min_crawls=s.stability_min_crawls,
            stretch_max=s.stretch_max,
            volatile_boost=s.volatile_boost,
            computed_at=now,
            summary=result.summary,
            decisions=result.decisions,
            warnings=warnings,
        )

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
            locale=project.locale,
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
            locale=project.locale,
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
        result = sampling_plan(project, prompts, self._db, self._positions, self._settings, now)
        due = {item.prompt.id: item.engines for item in result.items}
        return [self._result_for(project, p, due_on=due.get(p.id, [])) for p in prompts]

    def _result_for(
        self, project: Project, prompt: TrackedPrompt, *, due_on: list[Engine] | None = None
    ) -> PromptResult:
        """Latest state of one prompt. Shared by `results` and `prompt_detail`."""
        engines = effective_engines(project, prompt)
        snapshots: dict[str, CitationSnapshot | None] = {}
        for engine in engines:
            history = self._db.history(prompt.prompt_id, engine, limit=1)
            snapshots[engine.value] = history[0] if history else None
        organic_prompt = self._db.organic_history(prompt.prompt_id, RankQueryKind.PROMPT, limit=1)
        organic_keyword = self._db.organic_history(prompt.prompt_id, RankQueryKind.KEYWORD, limit=1)
        if due_on is None:
            result = sampling_plan(
                project, [prompt], self._db, self._positions, self._settings, self._clock()
            )
            due_on = result.items[0].engines if result.items else []
        return PromptResult(
            prompt=prompt,
            effective_interval=prompt.interval or project.interval,
            effective_engines=engines,
            snapshots=snapshots,
            organic_prompt=organic_prompt[0] if organic_prompt else None,
            organic_keyword=organic_keyword[0] if organic_keyword else None,
            due_on=due_on,
        )
