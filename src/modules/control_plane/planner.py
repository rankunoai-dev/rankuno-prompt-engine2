"""Derives due work from the time-series store.

A prompt is due on a platform when the newest snapshot for that prompt and
platform is older than the prompt's effective interval (its own override, else
the project's). No separate per-prompt schedule table exists: the history *is*
the schedule state, which means a run started by hand and a scheduled run are
indistinguishable to the planner, and a crash never leaves the schedule and the
data disagreeing.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from src.integrations.schemas import Engine
from src.modules.control_plane.schemas import DueItem, Project, TrackedPrompt, WorkBatch
from src.modules.prompt_tracking.scheduler import parse_interval
from src.modules.prompt_tracking.time_series_db import TimeSeriesDB

__all__ = ["batches", "due_items", "effective_engines", "effective_interval", "effective_samples"]


def effective_interval(project: Project, prompt: TrackedPrompt) -> timedelta:
    """The prompt's interval override, else the project's."""
    return parse_interval(prompt.interval or project.interval)


def effective_engines(project: Project, prompt: TrackedPrompt) -> list[Engine]:
    """The prompt's platform override, else the project's."""
    return list(prompt.engines or project.engines)


def effective_samples(project: Project, prompt: TrackedPrompt) -> int | None:
    """The prompt's sample override, else the project's (None = settings default)."""
    return prompt.samples_per_engine or project.samples_per_engine


def due_items(
    project: Project,
    prompts: list[TrackedPrompt],
    db: TimeSeriesDB,
    now: datetime,
    *,
    force: bool = False,
    only_ids: list[str] | None = None,
    engines_filter: list[Engine] | None = None,
) -> list[DueItem]:
    """Prompts (and the platforms) that need sampling now.

    Args:
        project: Owning project; a disabled project yields nothing unless `force`.
        prompts: Candidate prompts; disabled ones are skipped unless `force`.
        db: Store to read the newest snapshot per prompt/platform from.
        now: Current time (UTC).
        force: Treat everything selected as due.
        only_ids: Restrict to these tracked-prompt ids.
        engines_filter: Restrict to these platforms.
    """
    if not project.enabled and not force:
        return []
    wanted = set(only_ids) if only_ids else None
    items: list[DueItem] = []
    for prompt in prompts:
        if wanted is not None and prompt.id not in wanted:
            continue
        if not prompt.enabled and not force:
            continue
        engines = effective_engines(project, prompt)
        if engines_filter:
            engines = [e for e in engines if e in engines_filter]
        if not engines:
            continue
        interval = effective_interval(project, prompt)
        due: list[Engine] = []
        reasons: list[str] = []
        for engine in engines:
            history = db.history(prompt.prompt_id, engine, limit=1)
            if force:
                due.append(engine)
                reasons.append(f"{engine.value}: forced")
            elif not history:
                due.append(engine)
                reasons.append(f"{engine.value}: never sampled")
            elif history[0].captured_at <= now - interval:
                due.append(engine)
                reasons.append(f"{engine.value}: last {history[0].captured_at.date()}")
        if due:
            items.append(DueItem(prompt=prompt, engines=due, reason="; ".join(reasons)))
    return items


def batches(project: Project, items: list[DueItem]) -> list[WorkBatch]:
    """Group due items by (platform set, samples) so each group is one pipeline call.

    Important prompts come first within a batch, and batches containing an
    important prompt run first, so a budget stop hits the least important work.
    """
    grouped: dict[tuple[tuple[str, ...], int | None], list[TrackedPrompt]] = defaultdict(list)
    for item in items:
        key = (tuple(e.value for e in item.engines), effective_samples(project, item.prompt))
        grouped[key].append(item.prompt)
    out: list[WorkBatch] = []
    for (engine_values, samples), prompts in grouped.items():
        ordered = sorted(prompts, key=lambda p: (not p.important, p.created_at, p.id))
        out.append(
            WorkBatch(
                engines=[Engine(v) for v in engine_values],
                samples_per_engine=samples,
                prompts=ordered,
            )
        )
    out.sort(key=lambda b: (not any(p.important for p in b.prompts), -len(b.prompts)))
    return out
