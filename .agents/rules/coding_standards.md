# Coding Standards — Non-Negotiable Rules

These rules are binding for every agent and every change in this repository.
Where this file and any document in `docs/` disagree, **this file wins**.

---

## 1. Dependency Direction

**Inward-only dependencies**: `modules -> integrations -> core`.

- `core/` MUST NOT import from `integrations/` or `modules/`.
- `integrations/` MUST NOT import from `modules/`.

A violation is a **build failure**, not a review comment.

---

## 2. Schema Discipline

**No loose dicts across module boundaries.**

Every boundary uses a Pydantic model inheriting `StrictModel` (`src/core/schemas.py`).

`StrictModel` config: `extra="forbid"`, `validate_assignment=True`.
**Not** `frozen=True` — it breaks `validate_assignment`.

---

## 3. Configuration

**No direct `os.environ` reads.**

Configuration goes through `get_settings()` (`src/core/config.py`).
Credentials are `SecretStr`.

---

## 4. Logging

**No `print()`.**

Use `src.core.logger.get_logger(__name__)`. Enforced by ruff `T20`.

---

## 5. External API Calls

**No external API call outside a `BaseAPIClient` subclass** (`src/integrations/base_client.py`).

---

## 6. Quality Gate

**Never report a task complete without a green quality gate.**

```powershell
# Full gate: ruff format, ruff check, mypy --strict, pytest >=85% coverage
powershell -ExecutionPolicy Bypass -File .\scripts\verify.ps1

# Auto-fix formatting and lint, then run gate
powershell -ExecutionPolicy Bypass -File .\scripts\verify.ps1 -Fix

# Documentation drift audit
.\.venv\Scripts\python.exe scripts\drift_check.py
```

The gate must exit zero. Coverage floor is **85%** — may be raised, never lowered.

Scratch files (`*.py` written for one-off analysis) MUST be deleted before running
the gate — ruff lints the whole tree and will fail on them.

---

## 7. Enum Casing

- **Governance enums lowercase**: `RiskClass`, `ApprovalMode`, `ExecutionStatus`.
- **Domain taxonomy enums UPPER**: `HierarchyLevel`, `PrimaryPageType`, `SearchIntent`.

---

## 8. Style

Match the surrounding code. `src/core/` modules set the standard:

- Google-style docstrings explaining *why*, not *what*.
- Module docstrings stating the design stance.
- Comments reserved for non-obvious decisions.
- Target **< 400 lines per file**.
- No `TODO` without an issue number.
- No commented-out code.

---

## 9. Crawler Safety (mandatory before any outbound fetch)

Two controls in `core` are mandatory on every outbound fetch — neither is optional:

- **`UrlSafetyPolicy.validate()`** — no URL reaches an HTTP client without producing
  a `SafeUrl` first. Pin the connection to `SafeUrl.resolved_ips` to close the DNS
  rebinding window.
- **`robots.can_fetch()`** — checked per path, with `AsyncTokenBucket.from_crawl_delay()`
  honouring any declared `Crawl-delay`.
