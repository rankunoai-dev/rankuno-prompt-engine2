"""Command-line entry point: `python -m src.modules.prompt_tracking <command>`.

Commands:

* `run` — one tracking run for a client (generated and/or custom prompts).
* `schedule run-due --jobs jobs.json` — run every job whose interval elapsed.
  Invoke hourly from Task Scheduler / cron.
* `schedule daemon --jobs jobs.json` — poll and run due jobs in a loop.
* `schedule status --jobs jobs.json` — show last/next run per job.
* `schedule install-task --jobs jobs.json` — print the Windows Task Scheduler
  command that would register `run-due` hourly. Printed, never executed.

Approval model. The pipeline is FINANCIAL, so it is refused unless one of:

* `--approve-spend` — the operator running this command approves the spend,
  in writing, on the command line; or
* `UNATTENDED_SPEND_CAP_USD` > 0 in `.env` — pre-approved by budget through
  `BudgetedApprovalProvider` (the intended mode for scheduled runs).

Either way `MAX_SESSION_SPEND_USD` remains the hard ceiling.

For backwards compatibility, an invocation that starts with `--` is treated
as `run`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import BaseModel

from src.core.config import Settings, get_settings
from src.core.guardrails import (
    BudgetedApprovalProvider,
    CallbackApprovalProvider,
    GuardrailEngine,
)
from src.core.logger import get_logger
from src.core.rate_limiter import CostLedger
from src.core.schemas import ToolMetadata, ToolResult
from src.integrations.schemas import Engine
from src.integrations.usage import get_usage_ledger, usage_context
from src.modules.prompt_tracking.costing import build_cost_report, format_cost_report
from src.modules.prompt_tracking.inputs import read_prompts_file
from src.modules.prompt_tracking.pipeline import PromptTrackerPipeline
from src.modules.prompt_tracking.scheduler import Scheduler, TrackingJob, load_jobs
from src.modules.prompt_tracking.schemas import ClientProfile, CustomPrompt, PipelineInput
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB

__all__ = ["build_parser", "main"]

_logger = get_logger("modules.prompt_tracking.cli")


def build_parser() -> argparse.ArgumentParser:
    """CLI schema."""
    parser = argparse.ArgumentParser(
        prog="prompt_tracking",
        description="RankUno prompt research, multi-engine citation and Google rank tracker.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="One tracking run for a client.")
    run.add_argument("--lob", required=True, help="Line of business, e.g. 'Procurement Software'.")
    run.add_argument("--brand", required=True, help="Client brand name.")
    run.add_argument("--domain", action="append", required=True, help="Client domain (repeatable).")
    run.add_argument("--alias", action="append", default=[], help="Brand alias (repeatable).")
    run.add_argument("--competitor", action="append", default=[], help="Competitor domain.")
    run.add_argument(
        "--competitor-name",
        action="append",
        default=[],
        help="Competitor brand name for mention detection (repeatable).",
    )
    run.add_argument("--keyword", action="append", required=True, help="Seed keyword (repeatable).")
    run.add_argument("--subtopic", action="append", default=[], help="Analyst subtopic.")
    run.add_argument("--landing-pages", type=Path, help="Text file, one client URL per line.")
    run.add_argument("--prompt", action="append", default=[], help="Custom prompt (repeatable).")
    run.add_argument(
        "--prompts-file",
        type=Path,
        help="Prompts file: one per line, optional '| keyword | subtopic'.",
    )
    run.add_argument(
        "--also-generate",
        action="store_true",
        help="With custom prompts, still harvest Semrush and add the generated 10+10 set.",
    )
    run.add_argument(
        "--engine",
        action="append",
        choices=[e.value for e in Engine],
        help="Restrict to specific engines (repeatable). Default: all four.",
    )
    run.add_argument(
        "--openai-model", help="Override OpenAI Search model (e.g. gpt-4o, gpt-4o-mini)."
    )
    run.add_argument(
        "--perplexity-model",
        help="Override Perplexity model (e.g. sonar, google/gemini-3.6-flash).",
    )
    run.add_argument("--samples", type=int, help="Max samples per engine per prompt (1-10).")
    run.add_argument("--no-adaptive", action="store_true", help="Always take every sample.")
    run.add_argument("--max-calls", type=int, help="Hard cap on engine calls for this run.")
    run.add_argument("--reuse-hours", type=int, help="Reuse snapshots newer than this.")
    run.add_argument("--workers", type=int, help="Parallel engine calls (1-16).")
    run.add_argument("--resolve-redirects", action="store_true", help="Follow Gemini links.")
    run.add_argument("--skip-engine-audit", action="store_true", help="Research only.")
    run.add_argument("--no-keyword-rank", action="store_true", help="Skip keyword rank calls.")
    _common(run)

    costs = sub.add_parser("costs", help="Spend and volume per vendor from the usage ledger.")
    costs.add_argument("--days", type=int, default=None, help="Only calls from the last N days.")
    costs.add_argument("--run-id", action="append", default=[], help="Only these runs.")
    costs.add_argument("--json", action="store_true", help="Emit the report as JSON.")

    schedule = sub.add_parser("schedule", help="Interval scheduling of tracking jobs.")
    schedule_sub = schedule.add_subparsers(dest="schedule_command", required=True)
    for name, help_text in (
        ("run-due", "Run every enabled job whose interval has elapsed."),
        ("daemon", "Poll and run due jobs in a loop."),
        ("status", "Show last/next run per job."),
        ("install-task", "Print the Windows Task Scheduler registration command."),
    ):
        sp = schedule_sub.add_parser(name, help=help_text)
        sp.add_argument("--jobs", type=Path, required=True, help="Jobs JSON file.")
        if name == "run-due":
            sp.add_argument("--force", action="store_true", help="Run all jobs now.")
        if name == "daemon":
            sp.add_argument("--poll-minutes", type=float, default=15.0)
            sp.add_argument("--max-cycles", type=int, help="Stop after N cycles (tests).")
        if name in ("run-due", "daemon"):
            _common(sp)
        else:
            sp.add_argument("--json", action="store_true", help="Print as JSON.")
    return parser


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--approve-spend", action="store_true", help="Operator approves spend.")
    parser.add_argument("--json", action="store_true", help="Print the result as JSON.")


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0].startswith("-"):
        argv = ["run", *argv]
    args = build_parser().parse_args(argv)
    settings = get_settings()
    if args.command == "run":
        with usage_context(source="cli"):
            return _run(args, settings)
    if args.command == "costs":
        return _costs(args, settings)
    with usage_context(source="cli"):
        return _schedule(args, settings)


def _costs(args: argparse.Namespace, settings: Settings) -> int:
    report = build_cost_report(
        get_usage_ledger(settings), settings, days=args.days, run_ids=args.run_id or None
    )
    text = report.model_dump_json(indent=2) if args.json else format_cost_report(report)
    sys.stdout.write(text + "\n")
    return 0


# -- run -------------------------------------------------------------------


def _run(args: argparse.Namespace, settings: Settings) -> int:
    if getattr(args, "openai_model", None):
        settings.openai_search_model = args.openai_model
    if getattr(args, "perplexity_model", None):
        settings.perplexity_model = args.perplexity_model
    landing_pages: list[str] = []
    if args.landing_pages:
        landing_pages = [
            line.strip()
            for line in args.landing_pages.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    custom = [CustomPrompt(prompt_text=p) for p in args.prompt]
    if args.prompts_file:
        custom += read_prompts_file(args.prompts_file)

    client = ClientProfile(
        brand_name=args.brand,
        aliases=args.alias,
        domains=args.domain,
        competitor_domains=args.competitor,
        competitor_names=args.competitor_name,
        lob=args.lob,
        seed_keywords=args.keyword,
        subtopics=args.subtopic,
        landing_pages=landing_pages,
    )
    payload = PipelineInput(
        client=client,
        engines=[Engine(e) for e in args.engine] if args.engine else list(Engine),
        samples_per_engine=args.samples,
        resolve_redirects=args.resolve_redirects,
        skip_engine_audit=args.skip_engine_audit,
        track_keyword_rank=not args.no_keyword_rank,
        custom_prompts=custom,
        generate_prompts=not custom or args.also_generate,
        max_engine_calls=args.max_calls,
        reuse_within_hours=args.reuse_hours,
        adaptive_sampling=False if args.no_adaptive else None,
        max_workers=args.workers,
    )

    ledger = CostLedger()
    guardrails = _guardrails(args.approve_spend, settings, ledger)
    result = PromptTrackerPipeline(guardrails, ledger, settings=settings).run(payload)
    _print_result(result, as_json=args.json)
    return 0 if result.ok else 1


# -- schedule --------------------------------------------------------------


def _schedule(args: argparse.Namespace, settings: Settings) -> int:
    jobs = load_jobs(args.jobs)
    db = TimeSeriesDB(settings.tracker_db_path)

    if args.schedule_command == "install-task":
        python = Path(sys.executable)
        command = (
            f'schtasks /Create /SC HOURLY /TN "RankUnoPromptTracker" '
            f'/TR "\\"{python}\\" -m src.modules.prompt_tracking schedule run-due '
            f'--jobs \\"{args.jobs.resolve()}\\"" /ST 00:05'
        )
        note = (
            "Run from the repository root. Requires UNATTENDED_SPEND_CAP_USD > 0 in .env "
            "so scheduled runs are pre-approved by budget."
        )
        if args.json:
            sys.stdout.write(json.dumps({"command": command, "note": note}, indent=2) + "\n")
        else:
            sys.stdout.write(f"{command}\n\n{note}\n")
        return 0

    ledger = CostLedger()
    approve = bool(getattr(args, "approve_spend", False))
    guardrails = _guardrails(approve, settings, ledger)

    def runner(job: TrackingJob) -> ToolResult[BaseModel]:
        tool = PromptTrackerPipeline(guardrails, ledger, settings=settings, db=db)
        return tool.run(job.to_pipeline_input())

    scheduler = Scheduler(db, runner)

    if args.schedule_command == "status":
        statuses = scheduler.status(jobs)
        if args.json:
            sys.stdout.write(
                json.dumps([s.model_dump(mode="json") for s in statuses], indent=2) + "\n"
            )
        else:
            for st in statuses:
                flag = "DUE" if st.due else ("off" if not st.enabled else "waiting")
                sys.stdout.write(
                    f"{st.name:<24} {st.interval:<8} {flag:<8} "
                    f"last={st.last_run_at or '-'} status={st.last_status or '-'} "
                    f"next={st.next_run_at or '-'}\n"
                )
        return 0

    if args.schedule_command == "daemon":
        cycles = scheduler.daemon(jobs, poll_s=args.poll_minutes * 60.0, max_cycles=args.max_cycles)
        sys.stdout.write(f"Daemon finished after {cycles} cycle(s).\n")
        return 0

    outcomes = scheduler.run_due(jobs, force=args.force)
    if args.json:
        sys.stdout.write(json.dumps([o.model_dump(mode="json") for o in outcomes], indent=2) + "\n")
    else:
        for o in outcomes:
            line = f"{o.name}: {'ran' if o.ran else 'skipped'} ({o.reason})"
            if o.ran:
                line += f" status={o.status} run_id={o.run_id}"
            sys.stdout.write(line + "\n")
    failed = [o for o in outcomes if o.ran and o.status != "success"]
    return 1 if failed else 0


# -- shared ----------------------------------------------------------------


def _guardrails(approve: bool, settings: Settings, ledger: CostLedger) -> GuardrailEngine:
    if approve:

        def approver(metadata: ToolMetadata, context: str) -> bool:
            _logger.info("operator_cli_approval", extra={"tool": metadata.name, "context": context})
            return True

        return GuardrailEngine(CallbackApprovalProvider(approver), settings=settings)
    if settings.unattended_spend_cap_usd > 0:
        provider = BudgetedApprovalProvider(
            ledger, per_action_cap_usd=settings.unattended_spend_cap_usd
        )
        return GuardrailEngine(provider, settings=settings)
    return GuardrailEngine(settings=settings)


def _print_result(result: ToolResult[BaseModel], *, as_json: bool) -> None:
    if as_json:
        # `run()` returns ToolResult[BaseModel]; without serialize_as_any the
        # payload would be serialised against the bare base class as `{}`.
        data_str = result.model_dump_json(indent=2, serialize_as_any=True) + "\n"
        try:
            sys.stdout.write(data_str)
        except UnicodeEncodeError:
            sys.stdout.buffer.write(data_str.encode("utf-8"))
        return
    if result.ok and result.data is not None:
        d = result.data
        lines = [
            f"Run {getattr(d, 'run_id', '?')} finished with status {result.status.value}.",
            f"Prompts selected: {getattr(d, 'prompts_selected', 0)} "
            f"(custom {getattr(d, 'custom_prompts', 0)})",
            f"Engine calls: {getattr(d, 'engine_calls', 0)} "
            f"(failed {getattr(d, 'failed_engine_calls', 0)}, "
            f"reused {getattr(d, 'reused_snapshots', 0)}, "
            f"refused by circuit {getattr(d, 'calls_refused_by_circuit', 0)}); "
            f"keyword rank calls: {getattr(d, 'keyword_rank_calls', 0)}",
            f"Estimated spend: ${getattr(d, 'estimated_cost_usd', 0.0):.2f}",
            f"Report: {getattr(d, 'report_path', None) or 'not written'}",
            f"Dataset: {getattr(d, 'dataset_path', None) or 'not written'}",
        ]
        lines += [f"MODEL SHIFT: {shift}" for shift in getattr(d, "model_shifts", [])]
        lines += [f"WARNING: {warning}" for warning in getattr(d, "warnings", [])]
        sys.stdout.write("\n".join(lines) + "\n")
        return
    sys.stdout.write(json.dumps({"status": result.status.value, "error": result.error}) + "\n")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
