# 📐 SDLC Step 2 Standard: System Architecture & Data Schema Protocol

> **Document ID**: `RKN-STD-SDLC-STEP2-V1`  
> **Status**: Binding Standard  
> **Applies To**: All Rankuno AI Platform Microservices & Modules  

---

## 1. Overview & Purpose

Step 2 translates investigation findings into **Strict Pydantic v2 Data Contracts** and modular component topologies. Rankuno operates under a zero-loose-dict rule: no untyped Python dictionary may cross a module boundary.

---

## 2. Mandatory Data Contract Rules

### 2.1 Base Class Inheritance
* All data models MUST inherit from `StrictModel` (`src.core.schemas.StrictModel`), which enforces `extra="forbid"` and `validate_assignment=True`.
* Unknown or unexpected fields returned by external APIs MUST trigger validation errors rather than silently passing `None`.

### 2.2 Enum & Type Safety Conventions
* Enums MUST inherit from `str, Enum` (StrEnum) with `UPPER_SNAKE_CASE` members and explicit string values.
* Numeric fields MUST specify validation bounds using Pydantic `Field(ge=..., le=...)`. Confidence scores MUST be floats bounded in `[0.0, 1.0]`.

```python
from enum import Enum
from pydantic import Field
from src.core.schemas import StrictModel


class RiskClass(str, Enum):
    READ = "READ"
    DRAFT = "DRAFT"
    WRITE = "WRITE"
    FINANCIAL = "FINANCIAL"


class ToolMetadata(StrictModel):
    name: str
    version: str = "1.0.0"
    risk_class: RiskClass
    estimated_cost_usd: float = Field(default=0.0, ge=0.0)
```

### 2.3 The Dependency Rule (Inward Only)
The codebase topology enforces a strict inward-only dependency flow:
$$\text{modules} \longrightarrow \text{integrations} \longrightarrow \text{core}$$

* `src/core/` MUST NEVER import from `src/integrations/` or `src/modules/`.
* `src/integrations/` MAY import from `src/core/`, but MUST NEVER import from `src/modules/`.
* `src/modules/` MAY import from `src/integrations/` and `src/core/`.
* A violation of the Dependency Rule is an automatic build failure in CI.

---

## 3. Architecture Artifact Checklist

Before proceeding to Step 3 (HITL Architecture Review Checkpoint), the author MUST provide:

- [ ] **Pydantic v2 Schemas**: Fully typed `StrictModel` contracts for inputs, outputs, and intermediate states.
- [ ] **Tool Metadata Declaration**: Complete `ToolMetadata` declaring name, version, risk class, shared rate limit key, and estimated USD cost.
- [ ] **Component Topology Diagram**: Diagram or ASCII tree showing import direction respecting the Inward-Only Dependency Rule.
- [ ] **Architecture Decision Record (ADR)**: If introducing a new pattern, dependency, or DB schema change, an ADR MUST be created at `docs/adr/NNNN-title.md`.
