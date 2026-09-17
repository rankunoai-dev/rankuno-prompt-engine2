# 💻 SDLC Step 6 Standard: Step-by-Step Modular Implementation Protocol

> **Document ID**: `RKN-STD-SDLC-STEP6-V1`  
> **Status**: Binding Standard  
> **Applies To**: All Rankuno AI Platform Microservices & Modules  

---

## 1. Overview & Purpose

Step 6 governs **Clean, Modular Code Implementation**. Code MUST be written cleanly, strictly typed, fully documented, and built by inheriting standard platform base classes (`BaseTool`, `BaseIntegrationClient`, `RankunoError`).

---

## 2. Mandatory Implementation Rules

### 2.1 Base Tool Inheritance
All tools MUST inherit from `src.core.base_tool.BaseTool` and define:
- `metadata`: A `ToolMetadata` instance with name, version, risk class, shared rate limit key, and estimated USD cost.
- `input_model`: A `StrictModel` schema.
- `output_model`: A `StrictModel` schema.
- `_execute_impl(self, args)`: The core logic method (never called directly by external callers). External callers invoke `run(args)`, which runs the governed 10-step pipeline.

### 2.2 Error Handling Hierarchy
* Custom exceptions MUST inherit from `RankunoError` (`src.core.errors.RankunoError`) or its specialized subclasses (`GuardrailBlockedError`, `RateLimitExceededError`, `BudgetExceededError`, `IntegrationError`).
* Raw, unhandled exceptions MUST NOT leak outside a tool's boundary. `BaseTool.run()` catches exceptions and converts them into structured `ToolResult` error envelopes.

### 2.3 Logging & Tracing Standard
* Modules MUST use `src.core.logger.get_logger(__name__)`.
* `print()` calls are strictly banned and enforced by linter rule `T20`.
* Every log line MUST carry the context-bound `trace_id` for agent trajectory reconstruction.

---

## 3. Exit Criteria

- [ ] All code implemented in small, modular files (< 400 lines target).
- [ ] No `TODO` comments without an associated issue number.
- [ ] No commented-out dead code committed.
- [ ] Linter `ruff format` and `ruff check` pass cleanly without errors.
