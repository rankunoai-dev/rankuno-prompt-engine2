"""The 7-step master prompt tracker, as a governed `BaseTool`.

    1. Harvest seed demand from Semrush (volume + question phrases) — unless the
       run is custom-prompts-only.
    2. Generate branded and non-branded prompt candidates; wrap custom prompts.
    3. Run the 3-layer intent gate (advisory for custom prompts).
    4. Select 10 branded + 10 non-branded generated prompts, stage-balanced;
       custom prompts are always selected.
    5. Audit every selected prompt on every engine in a bounded thread pool,
       with adaptive sampling, a call cap, snapshot reuse and circuit awareness.
       The Google call also yields the prompt's organic rank; one extra call per
       distinct seed keyword yields the keyword's organic rank.
    6. Persist prompts, samples, snapshots and organic ranks to the store.
    7. Map landing pages, assign verdicts, write the master sheet.

Spend model: the tool is `RiskClass.FINANCIAL`. Its declared `estimated_cost_usd`
is a nominal reservation; the *actual* per-call cost of every engine request is
charged to the shared `CostLedger` before the call. `describe_invocation()`
states the projected total so the approver sees the real number. See ADR 0002.
"""

from __future__ import annotations

import contextvars
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.core.base_tool import BaseTool
from src.core.config import Settings, get_settings
from src.core.domains import registrable_domain
from src.core.errors import BudgetExceededError, RankunoError
from src.core.guardrails import GuardrailEngine
from src.core.logger import get_logger
from src.core.rate_limiter import CostLedger
from src.core.schemas import RiskClass, ToolMetadata
from src.integrations.gemini_search import GEMINI_REDIRECT_HOST, GeminiSearchClient
from src.integrations.openai_search import OpenAISearchClient
from src.integrations.perplexity import PerplexityClient
from src.integrations.schemas import Engine, EngineAnswer, KeywordRecord, KeywordSource
from src.integrations.semrush import SemrushClient
from src.integrations.serp_api import SerpApiClient
from src.integrations.url_resolver import RedirectResolver
from src.integrations.usage import current_usage_context, usage_context
from src.modules.prompt_tracking.assembly import answer_samples, build_record, model_shifts
from src.modules.prompt_tracking.audit import (
    AuditOutcome,
    CallCapReached,
    EngineLike,
    RunPlan,
    audit_engine,
)
from src.modules.prompt_tracking.organic import build_organic_snapshot
from src.modules.prompt_tracking.prompt_generator import PromptGenerator, kept
from src.modules.prompt_tracking.report import write_master_sheet, write_ui_dataset
from src.modules.prompt_tracking.schemas import (
    AnswerSample,
    CitationSnapshot,
    ClientProfile,
    MasterPromptRecord,
    OrganicRankSnapshot,
    PipelineInput,
    PipelinePhase,
    PipelineProgress,
    PromptCandidate,
    RankQueryKind,
    TrackerRunSummary,
    prompt_id_for,
)
from src.modules.prompt_tracking.selector import select_master_set
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB
from src.modules.prompt_tracking.url_mapper import UrlMapper

__all__ = ["EngineClient", "ProgressCallback", "PromptTrackerPipeline"]

_logger = get_logger("modules.prompt_tracking.pipeline")

EngineClient = EngineLike
"""Any connector exposing `ask(prompt) -> EngineAnswer` (all real connectors do)."""

ProgressCallback = Callable[[PipelineProgress], None]
"""Receives progress events; called from the thread running `execute()`."""


class PromptTrackerPipeline(BaseTool[PipelineInput, TrackerRunSummary]):
    """Runs the full prompt research and citation audit for one client LOB."""

    metadata = ToolMetadata(
        name="prompt_tracking.master_tracker",
        summary="Semrush-grounded 20-prompt research set with multi-engine citation audit.",
        risk_class=RiskClass.FINANCIAL,
        rate_limit_key=None,
        estimated_cost_usd=0.01,
    )
    input_model = PipelineInput
    output_model = TrackerRunSummary

    def __init__(
        self,
        guardrails: GuardrailEngine | None = None,
        cost_ledger: CostLedger | None = None,
        *,
        settings: Settings | None = None,
        semrush: SemrushClient | None = None,
        engines: dict[Engine, EngineClient] | None = None,
        resolver: RedirectResolver | None = None,
        db: TimeSeriesDB | None = None,
        clock: Callable[[], datetime] | None = None,
        progress: ProgressCallback | None = None,
    ) -> None:
        """Build the pipeline; every dependency is injectable for tests."""
        super().__init__(guardrails, cost_ledger)
        self._settings = settings or get_settings()
        self._semrush = semrush
        self._engines: dict[Engine, EngineClient] = dict(engines or {})
        self._resolver = resolver
        self._db = db
        self._clock = clock or (lambda: datetime.now(UTC))
        self._progress = progress
        self._generator = PromptGenerator()

    def _emit(self, phase: PipelinePhase, done: int, total: int, calls: int, msg: str) -> None:
        """Report progress to the injected callback, if any. Never raises into the run."""
        if self._progress is None:
            return
        event = PipelineProgress(phase=phase, done=done, total=total, calls=calls, message=msg)
        try:
            self._progress(event)
        except Exception:  # noqa: BLE001 - a UI sink must not abort a paid run
            _logger.exception("progress_callback_failed")

    # -- BaseTool contract -------------------------------------------------

    def describe_invocation(self, payload: PipelineInput) -> str:
        """State the projected spend so the approver sees a real number."""
        samples = payload.samples_per_engine or self._settings.samples_per_engine
        prompts = len(payload.custom_prompts) + (20 if payload.generate_prompts else 0)
        per_prompt = sum(self._cost_for(e) for e in payload.engines) * samples
        keywords = len(payload.client.seed_keywords)
        keyword_cost = keywords * self._settings.cost_serpapi_call_usd
        if payload.skip_engine_audit:
            projected, keyword_cost = 0.0, 0.0
        elif not payload.track_keyword_rank:
            projected, keyword_cost = per_prompt * prompts, 0.0
        else:
            projected = per_prompt * prompts
        return (
            f"Track LOB '{payload.client.lob}' for {payload.client.brand_name}: "
            f"{prompts} prompts x {len(payload.engines)} engines x up to {samples} samples, "
            f"projected engine spend ${projected:.2f} plus up to {keywords} keyword rank "
            f"calls (${keyword_cost:.2f}) plus Semrush units."
        )

    def execute(self, payload: PipelineInput) -> TrackerRunSummary:
        """Run all seven steps and return the summary."""
        run_id = uuid.uuid4().hex[:16]
        source = current_usage_context().get("source") or "pipeline"
        with usage_context(source=source, run_id=run_id):
            return self._execute(payload, run_id)

    def _execute(self, payload: PipelineInput, run_id: str) -> TrackerRunSummary:
        started = self._clock()
        client = payload.client
        warnings: list[str] = []
        s = self._settings

        # 1-4. Harvest, generate, gate, select.
        semrush = self._semrush or SemrushClient(s)
        seed_volumes: dict[str, int] = {}
        questions: dict[str, list[KeywordRecord]] = {}
        if payload.generate_prompts:
            self._emit(PipelinePhase.HARVEST, 0, 0, 0, "Harvesting seed keywords from Semrush")
            seed_volumes, questions = self._harvest(
                semrush, client.seed_keywords, payload, warnings
            )
        custom = self._generator.from_custom(client, payload.custom_prompts, seed_volumes)
        generated = (
            self._generator.from_keywords(client, seed_volumes, questions)
            if payload.generate_prompts
            else []
        )
        surviving = kept(generated)
        selected = list(custom)
        if payload.generate_prompts:
            selection = select_master_set(surviving)
            warnings.extend(selection.warnings)
            custom_keys = {c.prompt_text.lower() for c in custom}
            selected += [c for c in selection.selected if c.prompt_text.lower() not in custom_keys]
        _logger.info(
            "prompts_selected",
            extra={"custom": len(custom), "total": len(selected), "run_id": run_id},
        )

        # 5. Audit.
        db = self._db or TimeSeriesDB(s.tracker_db_path)
        adaptive = (
            s.adaptive_sampling if payload.adaptive_sampling is None else payload.adaptive_sampling
        )
        plan = RunPlan(
            self._ledger,
            max_calls=payload.max_engine_calls or s.max_engine_calls_per_run,
            adaptive=adaptive,
            min_samples=s.min_samples,
        )
        samples = payload.samples_per_engine or s.samples_per_engine
        reuse_hours = (
            s.reuse_within_hours
            if payload.reuse_within_hours is None
            else payload.reuse_within_hours
        )
        outcomes: dict[tuple[int, Engine], AuditOutcome | CitationSnapshot] = {}
        reused = engine_calls = failed_calls = 0
        spent = 0.0
        stop_reason: str | None = None

        if not payload.skip_engine_audit:
            tasks: list[tuple[int, Engine, PromptCandidate]] = []
            for idx, candidate in enumerate(selected):
                pid = prompt_id_for(client.lob, candidate.prompt_text)
                for engine in payload.engines:
                    recent = self._reusable(db, pid, engine, reuse_hours, started)
                    if recent is not None:
                        outcomes[(idx, engine)] = recent
                        reused += 1
                    else:
                        tasks.append((idx, engine, candidate))

            total_checks = len(tasks) + reused
            done_checks = reused
            self._emit(
                PipelinePhase.AUDIT,
                done_checks,
                total_checks,
                0,
                f"Auditing {len(selected)} prompt(s) on {len(payload.engines)} platform(s)",
            )
            workers = payload.max_workers or s.pipeline_max_workers
            context = contextvars.copy_context()  # usage tags travel into the pool
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures: dict[Future[AuditOutcome], tuple[int, Engine]] = {
                    pool.submit(
                        context.copy().run,
                        audit_engine,
                        self._engine(engine),
                        engine,
                        candidate,
                        client,
                        samples=samples,
                        cost_each=self._cost_for(engine),
                        plan=plan,
                        post_process=self._post_process(engine, payload.resolve_redirects),
                    ): (idx, engine)
                    for idx, engine, candidate in tasks
                }
                for future, key in futures.items():
                    done_checks += 1
                    try:
                        outcome = future.result()
                    except (BudgetExceededError, CallCapReached) as exc:
                        stop_reason = stop_reason or str(exc)
                        continue
                    finally:
                        self._emit(
                            PipelinePhase.AUDIT,
                            done_checks,
                            total_checks,
                            plan.calls,
                            f"{selected[key[0]].prompt_text[:60]} on {key[1].value}",
                        )
                    outcomes[key] = outcome
                    engine_calls += outcome.calls
                    failed_calls += outcome.failures
                    spent += outcome.cost
                    if outcome.stop_reason:
                        stop_reason = stop_reason or outcome.stop_reason
            if stop_reason:
                warnings.append(
                    f"Budget ceiling / call cap reached: {stop_reason} "
                    "Audit stopped early; records so far are kept."
                )

        # Keyword ranks: one lookup per distinct keyword.
        keyword_cache: dict[str, OrganicRankSnapshot | None] = {}
        keyword_calls = 0
        if payload.track_keyword_rank and not payload.skip_engine_audit and not stop_reason:
            self._emit(
                PipelinePhase.KEYWORD_RANKS,
                len(outcomes),
                len(outcomes),
                plan.calls,
                "Looking up Google organic rank for seed keywords",
            )
            for keyword in dict.fromkeys(c.core_keyword for c in selected):
                try:
                    kw_rank, cost, attempted = self._keyword_rank(keyword, client, warnings, plan)
                except (BudgetExceededError, CallCapReached) as exc:
                    warnings.append(f"Keyword rank lookups stopped: {exc}")
                    break
                keyword_cache[keyword] = kw_rank
                spent += cost
                keyword_calls += int(attempted)

        # 6-7. Assemble, persist, report.
        n = len(outcomes)
        self._emit(PipelinePhase.REPORT, n, n, plan.calls, "Persisting snapshots, writing report")
        mapper = UrlMapper(client.landing_pages)
        records: list[MasterPromptRecord] = []
        for idx, candidate in enumerate(selected):
            snapshots: list[CitationSnapshot] = []
            organic: list[OrganicRankSnapshot] = []
            samples_to_store: list[AnswerSample] = []
            for engine in payload.engines:
                item = outcomes.get((idx, engine))
                if item is None:
                    continue
                if isinstance(item, CitationSnapshot):
                    snapshots.append(item)
                    continue
                snapshots.append(item.snapshot)
                prompt_rank = build_organic_snapshot(
                    RankQueryKind.PROMPT, candidate.prompt_text, item.serps, client
                )
                if prompt_rank is not None:
                    organic.append(prompt_rank)
                samples_to_store.extend(answer_samples(item.answers, client, candidate))
            keyword_rank = keyword_cache.get(candidate.core_keyword)
            if keyword_rank is not None:
                organic.append(keyword_rank)

            record = build_record(candidate, snapshots, organic, client.lob, mapper)
            db.upsert_prompt(record, client.brand_name)
            for snapshot in snapshots:
                if not snapshot.reused:
                    db.record_snapshot(record.prompt_id, snapshot, run_id)
            for rank in organic:
                db.record_organic(record.prompt_id, rank, run_id)
            db.record_samples(record.prompt_id, samples_to_store, run_id)
            records.append(record)

        report_path: str | None = None
        dataset_path: str | None = None
        if payload.write_report and records:
            stamp = started.strftime("%Y%m%d-%H%M%S")
            slug = "".join(ch if ch.isalnum() else "-" for ch in client.lob.lower()).strip("-")
            base = Path(s.reports_dir) / f"master-prompts-{slug}-{stamp}"
            report_path = str(write_master_sheet(records, base.with_suffix(".csv")))
            dataset_path = str(write_ui_dataset(records, client, run_id, base.with_suffix(".json")))

        summary = TrackerRunSummary(
            run_id=run_id,
            lob=client.lob,
            brand_name=client.brand_name,
            started_at=started,
            finished_at=self._clock(),
            candidates_generated=len(generated) + len(custom),
            candidates_kept=len(surviving) + len(custom),
            prompts_selected=len(selected),
            engine_calls=engine_calls,
            failed_engine_calls=failed_calls,
            keyword_rank_calls=keyword_calls,
            reused_snapshots=reused,
            calls_refused_by_circuit=plan.refused,
            custom_prompts=len(custom),
            model_shifts=model_shifts(db, records, run_id),
            estimated_cost_usd=round(spent, 4),
            semrush_units=semrush.units_consumed,
            report_path=report_path,
            dataset_path=dataset_path,
            warnings=warnings,
            records=records,
        )
        db.record_run(summary)
        self._emit(PipelinePhase.DONE, n, n, plan.calls, f"Run {run_id} finished")
        return summary

    # -- steps ---------------------------------------------------------------

    def _harvest(
        self,
        semrush: SemrushClient,
        seeds: list[str],
        payload: PipelineInput,
        warnings: list[str],
    ) -> tuple[dict[str, int], dict[str, list[KeywordRecord]]]:
        """Volume for each seed and its question phrases; failures become warnings.

        Each report is reserved against the ledger *before* the call, at the
        upper bound Semrush can bill for it (rows × units per row). Charging once
        after the loop, as this used to, let a whole harvest run and be billed by
        the vendor before the ceiling saw a cent of it. A refused reservation ends
        the harvest with a warning instead of a bill.
        """
        volumes: dict[str, int] = {}
        questions: dict[str, list[KeywordRecord]] = {}
        unit_usd = self._settings.cost_semrush_unit_usd
        overview_usd = semrush.estimate_units(KeywordSource.PHRASE_ALL, 1) * unit_usd
        questions_usd = (
            semrush.estimate_units(KeywordSource.PHRASE_QUESTIONS, payload.questions_per_keyword)
            * unit_usd
        )
        reserved = 0.0
        for seed in seeds:
            try:
                self._ledger.charge(overview_usd)
                reserved += overview_usd
                overview = semrush.phrase_all(seed)
                volumes[seed] = overview.search_volume if overview else 0
                self._ledger.charge(questions_usd)
                reserved += questions_usd
                questions[seed] = semrush.phrase_questions(
                    seed, display_limit=payload.questions_per_keyword
                )
            except BudgetExceededError as exc:
                warnings.append(f"Semrush harvest stopped at '{seed}': {exc}")
                break
            except RankunoError as exc:
                warnings.append(f"Semrush harvest failed for '{seed}': {exc}")
        for seed in seeds:  # seeds skipped by a failure or a budget stop still need entries
            volumes.setdefault(seed, 0)
            questions.setdefault(seed, [])
        # Reports bill per row returned, so the upper-bound reservation is usually
        # more than the real bill; hand the difference back to the ledger.
        billed = semrush.units_consumed * unit_usd
        if reserved > billed:
            self._ledger.release(reserved - billed)
        return volumes, questions

    @staticmethod
    def _reusable(
        db: TimeSeriesDB, prompt_id: str, engine: Engine, hours: int, now: datetime
    ) -> CitationSnapshot | None:
        """A recent enough snapshot to reuse instead of re-sampling, marked as such."""
        if hours <= 0:
            return None
        history = db.history(prompt_id, engine, limit=1)
        if not history or history[0].captured_at < now - timedelta(hours=hours):
            return None
        return history[0].model_copy(update={"reused": True})

    def _post_process(
        self, engine: Engine, resolve_redirects: bool
    ) -> Callable[[EngineAnswer], None] | None:
        """Per-engine hook applied to each successful answer."""
        if engine is Engine.GEMINI and resolve_redirects:
            return self._resolve_unresolved
        return None

    def _keyword_rank(
        self, keyword: str, client: ClientProfile, warnings: list[str], plan: RunPlan
    ) -> tuple[OrganicRankSnapshot | None, float, bool]:
        """One SerpApi call for the seed keyword's classic organic ranking.

        Requires the real Google connector; an injected stand-in for the AI
        Overview engine cannot supply a SERP, so the lookup is skipped and logged.
        Returns (snapshot, cost, attempted).
        """
        connector = self._engine(Engine.GOOGLE_AI_OVERVIEW)
        if not isinstance(connector, SerpApiClient):
            _logger.info("keyword_rank_skipped_no_serp_connector", extra={"keyword": keyword})
            return None, 0.0, False
        if not connector.available:
            warnings.append(f"Keyword rank lookup skipped for '{keyword}': SerpApi circuit open.")
            return None, 0.0, False
        cost = self._cost_for(Engine.GOOGLE_AI_OVERVIEW)
        plan.reserve(cost)
        try:
            with usage_context(engine="KEYWORD_RANK", prompt_id=None):
                serp = connector.search(keyword)
        except RankunoError as exc:
            warnings.append(f"Keyword rank lookup failed for '{keyword}': {exc}")
            return None, cost, True
        return build_organic_snapshot(RankQueryKind.KEYWORD, keyword, [serp], client), cost, True

    def _resolve_unresolved(self, answer: EngineAnswer) -> None:
        """Follow Gemini redirect links so `domain` names the real source."""
        resolver = self._resolver or RedirectResolver(self._settings)
        self._resolver = resolver
        remap: dict[str, str] = {}
        for citation in answer.citations:
            if citation.resolved or GEMINI_REDIRECT_HOST not in citation.url:
                continue
            result = resolver.resolve(citation.url)
            if result.resolved:
                domain = registrable_domain(result.final_url)
                if domain:
                    remap[citation.url] = result.final_url
                    citation.url = result.final_url
                    citation.domain = domain
                    citation.resolved = True
        for claim in answer.citation_claims:
            claim.url = remap.get(claim.url, claim.url)
        for snippet in answer.source_snippets:
            snippet.url = remap.get(snippet.url, snippet.url)

    # -- helpers -----------------------------------------------------------

    def _engine(self, engine: Engine) -> EngineClient:
        """Return (building lazily) the connector for `engine`."""
        connector = self._engines.get(engine)
        if connector is None:
            builders: dict[Engine, Callable[[], EngineClient]] = {
                Engine.CHATGPT_SEARCH: lambda: OpenAISearchClient(self._settings),
                Engine.PERPLEXITY: lambda: PerplexityClient(self._settings),
                Engine.GEMINI: lambda: GeminiSearchClient(self._settings),
                Engine.GOOGLE_AI_OVERVIEW: lambda: SerpApiClient(self._settings),
            }
            connector = builders[engine]()
            self._engines[engine] = connector
        return connector

    def _cost_for(self, engine: Engine) -> float:
        """Configured per-call cost estimate for `engine`."""
        s = self._settings
        return {
            Engine.CHATGPT_SEARCH: s.cost_openai_search_call_usd,
            Engine.PERPLEXITY: s.cost_perplexity_call_usd,
            Engine.GEMINI: s.cost_gemini_grounded_call_usd,
            Engine.GOOGLE_AI_OVERVIEW: s.cost_serpapi_call_usd,
        }[engine]
