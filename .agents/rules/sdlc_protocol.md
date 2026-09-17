# SDLC Protocol

Every feature, fix, or refactor follows the 8-step loop below.
The steps agents most often skip — and must not — are called out explicitly.

---

## The 8-Step Loop

### Step 1 — Scope
Define the problem, acceptance criteria, and the only files that need to change.
Write this down before touching code.

### Step 2 — Research
Read the relevant code. Do not assume. Check KIs, ADRs, and build-log entries
from prior cycles. Understand what already exists before designing anything new.

### Step 3 — HITL Review (STOP)
**Present the architecture before writing implementation code.**
Do not proceed on assumed approval. The plan must be shown and approved.

Required in the plan:
- Which files change and why
- Any new dependencies
- Risk class of each action (see `risk_classes.md`)
- Security/cost answers if the change touches the network or spends money (Step 5)

### Step 4 — Implementation
Write the code. Follow all rules in `coding_standards.md`.
Delete any scratch files before proceeding to Step 7.

### Step 5 — Security / Cost Audit
Answer the 8 questions from `docs/standards/SDLC_STEP5_SECURITY_FINANCIAL_AUDIT_STANDARD.md`
**before** coding anything that touches the network or spends money.

### Step 6 — Tests
Write tests that mirror `src/` package-for-package under `tests/`.
All external calls must be mocked.
Coverage floor is 85% — may be raised, never lowered.

### Step 7 — Verification (run the gate, paste output)
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify.ps1
```
The gate must exit zero. Paste the real output — do not summarise from memory.

### Step 8 — Drift Audit + Build Log

**8a — Drift audit** (same change as the code):
- Update `README.md` and `docs/ARCHITECTURE.md`.
- Add an ADR to `docs/adr/` for any consequential decision.
- Run: `.\.venv\Scripts\python.exe scripts\drift_check.py`

**8b — Build log** (mandatory, not optional):
Write a cycle entry to `docs/build-log/NNNN-<slug>.md` and register it in
`docs/build-log/README.md`.

Required build-log sections (these are the ones most likely to be skipped):
- **Bugs found and fixed** — including spec bugs; note when a failing test was wrong.
- **Corrections** — anything previously stated that turned out to be false.
  Never edit an old entry; correct it in the new one.
- **Explicitly not done** — so a later reader does not mistake a declared contract
  for an implemented one.

Paste real gate output. Do not summarise numbers from memory.

---

## Why the Build Log Is Mandatory

ADRs record what was decided; git records what changed. Neither records **why the
code is shaped the way it is, what broke on the way, and what was deliberately
left undone** — and in an AI-assisted codebase every session starts with no memory
of the last one, so that reasoning is lost by default rather than by accident.
