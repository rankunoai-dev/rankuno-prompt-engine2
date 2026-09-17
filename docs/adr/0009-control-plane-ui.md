# ADR 0009 — Control plane: projects, prompt-level scheduling, local UI

**Status**: Accepted (2026-09-17), operator request: a separate UI to create,
edit and delete projects; upload inputs per project; choose platforms; set the
run interval (daily, every 2 or 3 days, weekly, monthly, anything); star the
most important prompts and give them their own interval and platforms.

## Context

Configuration lived in CLI flags and a hand-edited jobs file. Scheduling was
per job, not per prompt. Analysts need to manage several clients, adjust
cadence per prompt, and see results without leaving a browser.

## Decisions

1. **New module `src/modules/control_plane`** (FastAPI + a single-file
   vanilla-JS page). It depends on `prompt_tracking` and `core`, never the
   reverse. FastAPI/uvicorn live in the `ui` extra so the engine still installs
   without them.
2. **Project = client + platforms + interval + prompts.** Stored as JSON blobs
   in two new tables (`projects`, `project_prompts`) in the same SQLite file.
   Prompts carry `important`, `enabled`, and optional overrides for `interval`,
   `engines`, `samples_per_engine`. Prompt text is kept verbatim.
3. **Due work is derived, not stored.** A prompt is due on a platform when its
   newest snapshot for that platform is older than its effective interval. No
   per-prompt schedule table, so manual and scheduled runs are
   indistinguishable and a crash cannot desynchronise schedule and data.
4. **Batches reuse the pipeline unchanged.** Due prompts are grouped by
   (platform set, samples) and each group becomes one `PipelineInput` with
   `custom_prompts`, so approval, budget, call cap, reuse, breakers and mention
   detection all apply. Important prompts are ordered first so a budget stop
   hits them last.
5. **Approval in the UI.** A "Run now" click is an operator action; the server
   started with `--approve-spend` treats every run from that process as
   approved. Without it, `UNATTENDED_SPEND_CAP_USD` gates runs by budget;
   otherwise runs are refused and the UI shows `blocked_pending_approval`.
6. **Files are read in the browser** and posted as text, so no multipart
   dependency is needed and the same import path serves paste and upload.

## Alternatives considered

- **Extend the existing `prompt-atlas.html`**: it is a read-only explorer from
  another session with its own data contract; mixing configuration into it
  would couple two unrelated pages.
- **Per-prompt schedule table**: more explicit, but duplicates what snapshot
  history already says and can drift from it.
- **A JS framework**: unnecessary for one page; vanilla keeps the build
  toolchain at zero.

## Consequences

- Two configuration paths exist (jobs file for `schedule` CLI, projects for
  the UI). The jobs file remains for headless deployments.
- Intervals stay elapsed-time based (see ADR 0007).
