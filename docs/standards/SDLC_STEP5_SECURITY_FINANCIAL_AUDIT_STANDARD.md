# 🔐 SDLC Step 5 Standard: Security, Rate-Limit, Financial & Edge-Case Audit Protocol

> **Document ID**: `RKN-STD-SDLC-STEP5-V1`  
> **Status**: Binding Standard  
> **Applies To**: All Rankuno AI Platform Microservices & Modules  

---

## 1. Overview & Purpose

Step 5 is the **Pre-Execution Security & Financial Guardrail Audit**. Every code change MUST pass an 8-point audit checklist to ensure it will not cause unapproved financial spend, rate-limit starvation, PII leakage, or unhandled upstream API outages.

---

## 2. Mandatory Step 5 Audit Checklist

Every Pull Request MUST answer these 8 audit questions in the PR description:

```markdown
### 🔒 Step 5 Security & Financial Audit Checklist

1. **Target Hosts & Volume**: Which external API endpoints or web domains does this touch, and at what request volume?
2. **Rate Limit Allocation**: Which `rate_limit_key` in `rate_limiter.py` does it share? Could it starve another parallel tool of quota?
3. **Worst-Case Financial Spend**: What is the maximum estimated USD cost of 1 invocation, and of a 10,000-item batch run? Is it charged to `CostLedger`?
4. **Idempotency Protection**: For `RiskClass.WRITE` and `RiskClass.FINANCIAL` operations, is a UUID `idempotency_key` required to prevent double-mutations on retry?
5. **Circuit Breaker Coverage**: Is the upstream API call protected by a Circuit Breaker (`CLOSED` ➔ `OPEN` ➔ `HALF-OPEN`) to handle 5xx spikes or vendor outages?
6. **PII & Data Minimisation**: Does it read, log, or store PII (IP addresses, user emails, personal data)? Are retention and redaction rules enforced?
7. **Input Sanitize & Injection Defense**: Is all external data (scraped HTML, API text) validated through a Pydantic `StrictModel` before processing? Is prompt injection isolated?
8. **Credential & Secret Protection**: Are all credentials loaded via `get_settings()` (typed `SecretStr`)? Are raw tokens or service account JSON files excluded from git?
```

---

## 3. Exit Criteria

- [ ] All 8 audit questions answered in writing.
- [ ] Non-zero cost tools marked `RiskClass.FINANCIAL` and charged to `CostLedger`.
- [ ] No hardcoded secrets or raw `os.environ` reads introduced.
