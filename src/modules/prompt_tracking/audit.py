"""Sampling one prompt on one engine, under a shared run plan.

Three controls keep engine spend down without losing statistical footing:

* **Adaptive sampling.** At least `min_samples` answers are taken; sampling then
  stops early once every answer so far agrees on whether the client was cited.
  Disagreement means the prompt is genuinely unstable and the full sample count
  is spent, which is exactly where the extra samples are worth paying for.
* **Call reservation.** Every call is reserved against the run's call cap and the
  `CostLedger` *before* it is made, from any worker thread, under one lock.
* **Circuit awareness.** A vendor whose breaker is open is not called at all;
  the remaining samples are recorded as refused, at no cost.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from src.core.errors import BudgetExceededError, RankunoError
from src.core.logger import get_logger
from src.core.rate_limiter import CostLedger
from src.integrations.schemas import Engine, EngineAnswer, SerpSnapshot
from src.integrations.serp_api import SerpApiClient
from src.integrations.usage import usage_context
from src.modules.prompt_tracking.citations import build_snapshot, client_rank
from src.modules.prompt_tracking.schemas import (
    CitationSnapshot,
    ClientProfile,
    PromptCandidate,
    prompt_id_for,
)

__all__ = ["AuditOutcome", "CallCapReached", "EngineLike", "RunPlan", "audit_engine", "consensus"]

_logger = get_logger("modules.prompt_tracking.audit")


class EngineLike(Protocol):
    """Structural type: anything with `ask(prompt) -> EngineAnswer`.

    Real connectors are `BaseAPIClient` subclasses (and expose `available`);
    tests inject stand-ins that need only `ask`.
    """

    def ask(self, prompt: str) -> EngineAnswer:
        """Answer `prompt`."""
        ...


class CallCapReached(RankunoError):
    """The run's engine-call cap has been reached."""

    def __init__(self, cap: int) -> None:
        """Record the cap that was hit."""
        self.cap = cap
        super().__init__(f"Engine call cap of {cap} reached for this run.")


class RunPlan:
    """Thread-safe reservation of engine calls against cap and budget."""

    def __init__(
        self,
        ledger: CostLedger,
        *,
        max_calls: int = 0,
        adaptive: bool = True,
        min_samples: int = 2,
    ) -> None:
        """Build a plan.

        Args:
            ledger: Budget to charge for each reserved call.
            max_calls: Cap on calls this run. Zero means unlimited.
            adaptive: Enable early-stop sampling.
            min_samples: Samples before early-stop may apply.
        """
        self._ledger = ledger
        self._max_calls = max_calls
        self.adaptive = adaptive
        self.min_samples = min_samples
        self._lock = threading.Lock()
        self.calls = 0
        self.refused = 0

    def reserve(self, cost_usd: float) -> None:
        """Reserve one call, charging the ledger. Raises before any spend if refused."""
        with self._lock:
            if self._max_calls and self.calls >= self._max_calls:
                raise CallCapReached(self._max_calls)
            self._ledger.charge(cost_usd)  # BudgetExceededError propagates
            self.calls += 1

    def note_refused(self, count: int) -> None:
        """Count samples not attempted because a vendor breaker was open."""
        with self._lock:
            self.refused += count


@dataclass
class AuditOutcome:
    """What sampling one prompt on one engine produced."""

    snapshot: CitationSnapshot
    answers: list[EngineAnswer]
    calls: int
    failures: int
    cost: float
    serps: list[SerpSnapshot] = field(default_factory=list)
    circuit_refused: int = 0
    stop_reason: str | None = None
    """Set when the budget or call cap ended sampling after at least one paid call."""


def consensus(answers: list[EngineAnswer], client_domains: list[str]) -> bool:
    """True when every answer agrees on whether the client was cited."""
    verdicts = {client_rank(a, client_domains) is not None for a in answers}
    return len(verdicts) == 1


def audit_engine(
    connector: EngineLike,
    engine: Engine,
    candidate: PromptCandidate,
    client: ClientProfile,
    *,
    samples: int,
    cost_each: float,
    plan: RunPlan,
    post_process: Callable[[EngineAnswer], None] | None = None,
) -> AuditOutcome:
    """Sample `candidate` on `engine` up to `samples` times.

    Raises:
        CallCapReached, BudgetExceededError: From `plan.reserve()` when the very
            first sample is refused. Once at least one sample has been paid for,
            a refusal ends sampling instead and is reported via `stop_reason`,
            so paid answers are never thrown away.
    """
    answers: list[EngineAnswer] = []
    serps: list[SerpSnapshot] = []
    failures = 0
    calls = 0
    spent = 0.0
    refused = 0
    stop_reason: str | None = None

    for taken in range(samples):
        if not getattr(connector, "available", True):
            refused = samples - taken
            plan.note_refused(refused)
            _logger.warning(
                "engine_skipped_circuit_open",
                extra={"engine": engine.value, "prompt": candidate.prompt_text, "refused": refused},
            )
            break

        try:
            plan.reserve(cost_each)
        except (CallCapReached, BudgetExceededError) as exc:
            if calls == 0:
                raise
            stop_reason = str(exc)
            break
        calls += 1
        spent += cost_each
        try:
            with usage_context(
                prompt_id=prompt_id_for(client.lob, candidate.prompt_text), engine=engine.value
            ):
                if isinstance(connector, SerpApiClient):
                    serp, answer = connector.search_and_ask(candidate.prompt_text)
                    serps.append(serp)
                else:
                    answer = connector.ask(candidate.prompt_text)
        except RankunoError as exc:
            failures += 1
            _logger.warning(
                "engine_call_failed",
                extra={"engine": engine.value, "prompt": candidate.prompt_text, "error": str(exc)},
            )
            continue

        if post_process is not None:
            post_process(answer)
        answers.append(answer)

        if (
            plan.adaptive
            and len(answers) >= plan.min_samples
            and consensus(answers, client.domains)
        ):
            break

    if not answers and failures == 0:
        # Nothing attempted (breaker open from the first sample): record one refused sample.
        failures = 1
    snapshot = build_snapshot(engine, answers, client, failed_samples=failures)
    snapshot.response_ids = [a.response_id for a in answers if a.response_id]
    return AuditOutcome(snapshot, answers, calls, failures, spent, serps, refused, stop_reason)
