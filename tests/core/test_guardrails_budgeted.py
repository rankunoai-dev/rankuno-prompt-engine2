"""Tests for pre-approved, budget-bounded unattended spend."""

from __future__ import annotations

import pytest

from src.core.guardrails import BudgetedApprovalProvider, GuardrailEngine
from src.core.rate_limiter import CostLedger
from src.core.schemas import RiskClass, ToolMetadata


def _paid(cost: float) -> ToolMetadata:
    return ToolMetadata(
        name="test.paid", summary="x", risk_class=RiskClass.FINANCIAL, estimated_cost_usd=cost
    )


def test_approves_financial_action_within_cap_and_ledger():
    provider = BudgetedApprovalProvider(CostLedger(ceiling_usd=5.0), per_action_cap_usd=1.0)
    assert provider.request_approval(_paid(0.5), "ctx") is True


def test_denies_action_above_per_action_cap():
    provider = BudgetedApprovalProvider(CostLedger(ceiling_usd=50.0), per_action_cap_usd=1.0)
    assert provider.request_approval(_paid(1.01), "ctx") is False


def test_denies_action_that_would_exceed_ledger_headroom():
    ledger = CostLedger(ceiling_usd=1.0)
    ledger.charge(0.8)
    provider = BudgetedApprovalProvider(ledger, per_action_cap_usd=5.0)
    assert provider.request_approval(_paid(0.5), "ctx") is False


def test_zero_cap_disables_all_approvals():
    provider = BudgetedApprovalProvider(CostLedger(ceiling_usd=5.0), per_action_cap_usd=0.0)
    assert provider.request_approval(_paid(0.0), "ctx") is False


def test_never_approves_write_actions():
    """A spend budget is not consent to mutate a client site."""
    provider = BudgetedApprovalProvider(CostLedger(ceiling_usd=5.0), per_action_cap_usd=5.0)
    write = ToolMetadata(name="test.write", summary="x", risk_class=RiskClass.WRITE)
    assert provider.request_approval(write, "ctx") is False


def test_negative_cap_is_rejected():
    with pytest.raises(ValueError, match="negative"):
        BudgetedApprovalProvider(CostLedger(ceiling_usd=1.0), per_action_cap_usd=-1.0)


def test_engine_integration_grants_financial_and_blocks_write(settings):
    provider = BudgetedApprovalProvider(CostLedger(ceiling_usd=5.0), per_action_cap_usd=1.0)
    engine = GuardrailEngine(provider, settings=settings)
    assert engine.authorize(_paid(0.2)).allowed is True
    write = ToolMetadata(name="test.write", summary="x", risk_class=RiskClass.WRITE)
    assert engine.authorize(write).allowed is False
