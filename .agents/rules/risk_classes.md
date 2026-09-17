# Risk Classes and HITL

Every tool declares a `RiskClass` in its `ToolMetadata`. There is no default.

---

## Risk Class Table

| Risk Class  | Approval Mode     | Behavior                                                              |
| :---------- | :---------------- | :-------------------------------------------------------------------- |
| `read`      | `automatic`       | Unattended execution permitted                                        |
| `draft`     | `operator_review` | Executes; output flagged `requires_human_review`                      |
| `write`     | `mandatory_hitl`  | Blocked until a human approves                                        |
| `financial` | `mandatory_hitl`  | Blocked until a human approves; charges `CostLedger`                  |

---

## Guardrail Engine Rules

- **Deny-by-default**: with no approval provider wired in, a `mandatory_hitl` action
  is refused, never auto-approved.
- `AutoApproveProvider` is **test-only** — using it outside `ENVIRONMENT=development`
  is a security defect.

---

## Enum Casing

Governance enums are **lowercase** to match shipped code:
`RiskClass`, `ApprovalMode`, `ExecutionStatus`.
