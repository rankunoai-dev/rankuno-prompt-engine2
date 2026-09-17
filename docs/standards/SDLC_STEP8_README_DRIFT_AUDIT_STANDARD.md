# 📚 SDLC Step 8 Standard: README & Architecture Drift Audit Protocol

> **Document ID**: `RKN-STD-SDLC-STEP8-V1`  
> **Status**: Binding Standard  
> **Applies To**: All Rankuno AI Platform Microservices & Modules  

---

## 1. Overview & Purpose

Step 8 closes the SDLC loop by preventing **Documentation Drift**. Any modification to modules, tools, schemas, environment variables, or API clients MUST be reflected immediately in `README.md` and `docs/ARCHITECTURE.md` within the exact same commit or Pull Request.

---

## 2. Mandatory Documentation Drift Rules

### 2.1 Single Source of Truth
* `README.md` and `docs/ARCHITECTURE.md` describe the **current, verified state** of capabilities.
* They MUST NEVER contain aspirational or un-implemented features marked as working.

### 2.2 Same-PR Update Obligation
* A PR modifying or adding a tool, Pydantic model, configuration key, or microservice MUST update `README.md` and `docs/ARCHITECTURE.md`.
* The CI job `docs-drift.yml` automatically checks for structural code edits without corresponding documentation changes and fails the build if drift is detected.

### 2.3 Architecture Decision Records (ADRs)
* Any consequential architectural choice (e.g. adding a new database, selecting a primary vector index, changing signal consensus weightings) MUST add a new ADR at `docs/adr/NNNN-<short-title>.md`.
* ADRs follow the template: *Context, Decision, Alternatives Considered, Consequences, Status*.

---

## 3. Exit Criteria

- [ ] `README.md` updated to reflect new capabilities, CLI commands, or environment variables.
- [ ] `docs/ARCHITECTURE.md` updated with new module structure or data flows.
- [ ] ADR added if the change made an architectural decision.
- [ ] Documentation drift audit passed cleanly.
