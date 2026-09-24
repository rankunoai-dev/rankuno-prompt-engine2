"""Due-work derivation from snapshot history."""

from __future__ import annotations

from datetime import timedelta

from src.integrations.schemas import Engine
from src.modules.control_plane.planner import (
    batches,
    due_items,
    effective_engines,
    effective_interval,
    effective_samples,
)
from src.modules.control_plane.schemas import (
    PairOverride,
    ProjectUpdate,
    TrackedPromptCreate,
    TrackedPromptUpdate,
)
from src.modules.prompt_tracking.schemas import (
    CitationSnapshot,
    DecisionStage,
    MasterPromptRecord,
    PromptType,
    SearchIntent,
    Verdict,
)
from tests.modules.control_plane.conftest import NOW


def _seed(db, prompt, engine, at):
    db.upsert_prompt(
        MasterPromptRecord(
            prompt_id=prompt.prompt_id,
            lob="Procurement Software",
            subtopic="S",
            core_keyword="k",
            search_volume=0,
            prompt_text=prompt.prompt_text,
            search_intent=SearchIntent.INFORMATIONAL,
            decision_stage=DecisionStage.AWARENESS,
            prompt_type=PromptType.NON_BRANDED,
            web_triggers=True,
            verdict=Verdict.KEEP,
            verdict_reason="x",
        ),
        "GEP",
    )
    db.record_snapshot(
        prompt.prompt_id,
        CitationSnapshot(
            engine=engine,
            model="m",
            captured_at=at,
            samples=1,
            web_trigger_rate=1.0,
            client_cited_samples=0,
            client_citation_rate=0.0,
            client_cited=False,
        ),
        "r",
    )


def test_effective_values_fall_back_to_project(store, project, prompts):
    plain, starred = prompts[0], prompts[1]
    assert effective_interval(project, plain) == timedelta(days=1)
    assert effective_interval(project, starred) == timedelta(days=2)
    assert effective_engines(project, plain) == list(Engine)
    assert effective_samples(project, plain) is None
    project2 = store.update_project(project.id, ProjectUpdate(samples_per_engine=3))
    assert effective_samples(project2, plain) == 3
    starred2 = store.update_prompt(
        project.id, starred.id, TrackedPromptUpdate(samples_per_engine=5, engines=[Engine.GEMINI])
    )
    assert effective_samples(project2, starred2) == 5
    assert effective_engines(project2, starred2) == [Engine.GEMINI]


def test_never_sampled_prompts_are_due_on_every_platform(project, prompts, db):
    items = due_items(project, prompts, db, NOW)
    assert len(items) == 2
    assert all(item.engines == list(Engine) for item in items)
    assert "never sampled" in items[0].reason


def test_due_respects_interval_per_platform(project, prompts, db):
    plain = prompts[0]
    _seed(db, plain, Engine.PERPLEXITY, NOW - timedelta(hours=2))  # fresh
    _seed(db, plain, Engine.GEMINI, NOW - timedelta(days=2))  # stale
    items = {i.prompt.id: i for i in due_items(project, [plain], db, NOW)}
    engines = items[plain.id].engines
    assert Engine.PERPLEXITY not in engines
    assert Engine.GEMINI in engines
    assert Engine.CHATGPT_SEARCH in engines  # never sampled


def test_prompt_interval_override_and_project_disabled(store, project, prompts, db):
    starred = prompts[1]  # every 2d
    _seed(db, starred, Engine.PERPLEXITY, NOW - timedelta(days=1, hours=12))
    only = due_items(project, [starred], db, NOW, engines_filter=[Engine.PERPLEXITY])
    assert only == []  # 36h < 2d
    _seed(db, starred, Engine.PERPLEXITY, NOW - timedelta(days=3))
    # newest snapshot is still the 36h one -> still not due
    assert due_items(project, [starred], db, NOW, engines_filter=[Engine.PERPLEXITY]) == []

    paused = store.update_project(project.id, ProjectUpdate(enabled=False))
    assert due_items(paused, prompts, db, NOW) == []
    assert len(due_items(paused, prompts, db, NOW, force=True)) == 2


def test_disabled_prompt_and_filters(store, project, prompts, db):
    off = store.update_prompt(project.id, prompts[0].id, TrackedPromptUpdate(enabled=False))
    items = due_items(project, [off, prompts[1]], db, NOW)
    assert [i.prompt.id for i in items] == [prompts[1].id]
    forced = due_items(project, [off], db, NOW, force=True)
    assert len(forced) == 1 and "forced" in forced[0].reason
    subset = due_items(project, prompts, db, NOW, only_ids=[prompts[1].id])
    assert [i.prompt.id for i in subset] == [prompts[1].id]
    assert due_items(project, prompts, db, NOW, engines_filter=[]) != []  # empty filter = no filter


def test_batches_group_by_engines_and_samples_important_first(store, project, prompts, db):
    plain, starred = prompts
    starred2 = store.update_prompt(
        project.id, starred.id, TrackedPromptUpdate(samples_per_engine=5)
    )
    third = store.add_prompt(
        project.id,
        TrackedPromptCreate(prompt_text="third prompt text"),
    )
    items = due_items(project, [plain, third, starred2], db, NOW)
    groups = batches(project, items)
    assert len(groups) == 2
    assert groups[0].prompts[0].id == starred2.id  # important batch first
    assert groups[0].samples_per_engine == 5
    assert {p.id for p in groups[1].prompts} == {plain.id, third.id}
    assert groups[1].samples_per_engine is None
    assert groups[1].engines == list(Engine)


def test_overrides_stretch_the_interval_and_split_boosted_platforms(project, prompts, db):
    plain = prompts[0]
    _seed(db, plain, Engine.GEMINI, NOW - timedelta(days=1, hours=1))  # due daily, not at x2
    _seed(db, plain, Engine.PERPLEXITY, NOW - timedelta(days=3))
    overrides = {
        (plain.id, Engine.GEMINI.value): PairOverride(multiplier=2),
        (plain.id, Engine.PERPLEXITY.value): PairOverride(samples=5, note="volatile"),
    }
    items = due_items(project, [plain], db, NOW, overrides=overrides)
    item = items[0]
    assert Engine.GEMINI not in item.engines and Engine.PERPLEXITY in item.engines
    assert item.boosted == {Engine.PERPLEXITY.value: 5}
    assert "never sampled" in item.reason and "stretched" not in item.reason
    groups = batches(project, items)
    assert [(g.engines, g.samples_per_engine) for g in groups] == [
        ([Engine.GOOGLE_AI_OVERVIEW, Engine.CHATGPT_SEARCH], None),
        ([Engine.PERPLEXITY], 5),
    ]
    # Past the stretched interval the pair is due again and says so.
    later = due_items(project, [plain], db, NOW + timedelta(days=1), overrides=overrides)
    assert "GEMINI: last 2026-09-16, stretched x2" in later[0].reason
