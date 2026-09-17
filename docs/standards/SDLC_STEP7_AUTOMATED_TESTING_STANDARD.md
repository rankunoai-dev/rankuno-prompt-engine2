# 🧪 SDLC Step 7 Standard: Automated Verification & Testing Protocol

> **Document ID**: `RKN-STD-SDLC-STEP7-V1`  
> **Status**: Binding Standard  
> **Applies To**: All Rankuno AI Platform Microservices & Modules  

---

## 1. Overview & Purpose

Step 7 is the **Automated Quality Gate**. Editing or writing code is NOT completing a task. A task is completed ONLY when automated test suites and static analysis tools run and pass 100% cleanly.

---

## 2. Mandatory Verification Rules

### 2.1 The Cardinal Rule: Never Declare Success Without Passing Tests
* An AI agent or engineer MUST NOT report a task as complete without executing the local verification pipeline (`scripts/verify.ps1` or `pytest`) and presenting the clean output.

### 2.2 Local Verification Suite (`scripts/verify.ps1`)
The local quality gate runs 4 automated checks matching CI parity:
```powershell
1. ruff format --check .                             # Code formatting
2. ruff check --output-format=github .               # Static linting
3. mypy src --strict                                 # Strict type checking
4. pytest --cov=src --cov-report=term-missing        # Unit tests & 85% coverage floor
```

### 2.3 Unit Testing & Service Mocking Standards
* `tests/` MUST mirror `src/` package-for-package.
* All external API calls (Google Search Console, Google Ads, SERP APIs, LLM calls) MUST be mocked using `pytest-mock` or `responses`.
* Networked tests hitting live APIs MUST be marked `@pytest.mark.integration` and excluded from default CI runs.
* Unit test coverage MUST be $\ge 85\%$ and monotonically increasing over time.

---

## 3. Exit Criteria

- [ ] `scripts/verify.ps1` (or `pytest`) executed locally with 100% pass rate.
- [ ] Code coverage floor $\ge 85\%$ asserted.
- [ ] Output log attached to task completion summary.
