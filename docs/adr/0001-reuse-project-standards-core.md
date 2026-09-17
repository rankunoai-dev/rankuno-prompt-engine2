# ADR 0001 — Reuse the project-standards core layer verbatim

**Status**: Accepted (2026-09-16)

## Context

`.agents/rules/` in this repository mandates a specific governed core:
`StrictModel`, `BaseTool`, `BaseAPIClient`, `GuardrailEngine`, `CostLedger`,
`get_settings()`, the JSON logger and the verify gate. That code already exists,
tested, in `C:\Users\RankUno\Documents\project-standards\src\core`. The rules also
name `UrlSafetyPolicy`, `robots.can_fetch()` and a crawl-delay bucket, which did
**not** exist there.

## Decision

Copy `src/core/*` and `tests/core/*` from project-standards unchanged, then add
the missing pieces in this repository: `url_safety.py`, `robots.py`,
`domains.py`, `BudgetedApprovalProvider`, `UnsafeUrlError`,
`UpstreamClientError`, `TokenBucket.from_crawl_delay`, and the prompt-tracker
settings. The prompt engine is a self-contained repository; it does not import
project-standards at runtime.

## Alternatives considered

- **Depend on project-standards as a package.** Rejected: it is not published,
  has no version, and its `integrations/semrush.py` violates its own standards
  (loose `BaseModel`, `IntegrationError` called with the wrong arity).
- **Rewrite core from scratch.** Rejected: the rules reference these exact
  class names and behaviours; re-deriving them invites drift.

## Consequences

- Two copies of core exist. Fixes made here should be ported back deliberately.
- The copied `semrush.py` was **not** reused; it was rewritten (see ADR 0004).
