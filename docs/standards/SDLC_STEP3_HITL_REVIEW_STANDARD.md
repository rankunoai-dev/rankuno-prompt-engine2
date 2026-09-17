# 🛡️ SDLC Step 3 Standard: HITL Architecture Review Checkpoint Protocol

> **Document ID**: `RKN-STD-SDLC-STEP3-V1`  
> **Status**: Binding Standard  
> **Applies To**: All Rankuno AI Platform Microservices & Modules  

---

## 1. Overview & Purpose

Step 3 is the mandatory **Human-in-the-Loop (HITL) Gate**. Neither an autonomous AI agent nor a developer may proceed to write implementation code until the proposed architecture, data contracts, and risk classifications have been explicitly reviewed and approved in writing by the operator / AI Lead.

---

## 2. Mandatory Approval Rules

### 2.1 Deny-by-Default Execution Policy
* The policy engine in `src/core/guardrails.py` operates under a strict **deny-by-default** stance.
* If no approval provider is configured, any action classified as `RiskClass.WRITE` or `RiskClass.FINANCIAL` is **refused at runtime**, never silently auto-approved.

### 2.2 Approval Mode Matrix

| Risk Class | Operational Scope | Approval Mode | Runtime Behavior |
| :--- | :--- | :--- | :--- |
| **`READ`** | Data fetching, GSC queries, sitemap parsing, SERP read | `AUTOMATIC` | Executes unattended. |
| **`DRAFT`** | Generating content briefs, ad copy drafts, recommendations | `OPERATOR_REVIEW` | Executes; outputs flagged `requires_human_review=True`. |
| **`WRITE`** | CMS publishing, database mutations, config modifications | `MANDATORY_HITL` | Blocks until explicit human operator approval is granted. |
| **`FINANCIAL`** | Google Ads budget mutations, paid API credits, metered LLMs | `MANDATORY_HITL` | Blocks until human approval is granted AND charges `CostLedger`. |

### 2.3 Non-Negotiable Protection Rules
1. **Production Refuses Unsafe Boot**: If `GUARDRAILS_ENABLED=False` is set in production, `Settings.model_post_init` MUST raise a fatal `ConfigurationError` and halt system boot.
2. **`AutoApproveProvider` is Test-Only**: Using auto-approval providers in staging or production is a severe security violation.
3. **Audit Trail Logging**: Every approval or denial decision MUST be recorded in the append-only JSONL audit log with timestamp, operator ID, tool name, and estimated cost.

---

## 3. Step 3 Exit Criteria

- [ ] Architecture blueprint presented to operator / AI Lead.
- [ ] Explicit written sign-off ("APPROVED") recorded in PR description or implementation plan.
- [ ] All `RiskClass.WRITE` and `RiskClass.FINANCIAL` capabilities declared with appropriate guardrail protection.
