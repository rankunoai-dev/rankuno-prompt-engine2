# ADR 0002 — Budgeted approval for unattended FINANCIAL runs

**Status**: Accepted (2026-09-16)

## Context

Every engine call costs money, so the tracker is `RiskClass.FINANCIAL`, which
the governance matrix maps to `MANDATORY_HITL`. The default provider denies. A
daily scheduled audit (20 prompts × 4 engines × 3 samples = 240 metered calls)
cannot have a human approve each call, and `AutoApproveProvider` is explicitly
test-only.

## Decision

1. Add `BudgetedApprovalProvider(ledger, per_action_cap_usd)` to core. It
   approves a FINANCIAL action only if its declared cost is ≤ the per-action cap
   **and** fits the `CostLedger`'s remaining headroom. It never approves WRITE.
   It is enabled only by an operator setting `UNATTENDED_SPEND_CAP_USD > 0`.
2. The pipeline declares a nominal `estimated_cost_usd` (a reservation) and
   charges the **actual** configured per-call cost to the ledger before every
   engine request. `MAX_SESSION_SPEND_USD` therefore remains the hard ceiling.
   `describe_invocation()` states the projected total for the approver.
3. Interactive runs use `--approve-spend`, which is a human's written approval of
   that invocation, wired through `CallbackApprovalProvider`.

## Alternatives considered

- **Declare the full worst-case cost on `ToolMetadata`.** Rejected: the cost
  depends on runtime inputs (engines, samples, prompts) and a static worst case
  would exceed any sane session ceiling, blocking every run.
- **Relax `REQUIRE_APPROVAL_FOR_SPEND`.** Rejected: it downgrades *all* spend to
  operator-review with no budget bound.

## Consequences

- An unattended run's blast radius is exactly `MAX_SESSION_SPEND_USD`.
- A mid-run `BudgetExceededError` ends the audit with a warning; already
  collected snapshots are persisted, so partial runs still add history.
