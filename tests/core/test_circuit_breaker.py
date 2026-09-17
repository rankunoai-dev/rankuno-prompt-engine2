"""Tests for the vendor circuit breaker."""

from __future__ import annotations

import pytest

from src.core.circuit_breaker import CircuitBreaker, CircuitBreakerRegistry, CircuitState


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def breaker(clock) -> CircuitBreaker:
    return CircuitBreaker("vendor", failure_threshold=3, cooldown_s=60.0, clock=clock)


def test_starts_closed_and_allows(breaker):
    assert breaker.state is CircuitState.CLOSED
    assert breaker.allow() is True
    assert breaker.retry_after_s == 0.0


def test_opens_after_threshold_consecutive_failures(breaker):
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state is CircuitState.CLOSED
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    assert breaker.allow() is False
    assert breaker.retry_after_s == pytest.approx(60.0)


def test_success_resets_the_failure_count(breaker):
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state is CircuitState.CLOSED


def test_half_open_admits_exactly_one_trial(breaker, clock):
    for _ in range(3):
        breaker.record_failure()
    clock.now += 60.0
    assert breaker.state is CircuitState.HALF_OPEN
    assert breaker.allow() is True
    assert breaker.allow() is False  # trial in flight
    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED
    assert breaker.allow() is True


def test_failed_trial_reopens_immediately(breaker, clock):
    for _ in range(3):
        breaker.record_failure()
    clock.now += 61.0
    assert breaker.allow() is True
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    assert breaker.retry_after_s == pytest.approx(60.0)


def test_retry_after_counts_down(breaker, clock):
    for _ in range(3):
        breaker.record_failure()
    clock.now += 45.0
    assert breaker.retry_after_s == pytest.approx(15.0)


def test_reset_force_closes(breaker):
    for _ in range(3):
        breaker.record_failure()
    breaker.reset()
    assert breaker.state is CircuitState.CLOSED


@pytest.mark.parametrize(("threshold", "cooldown"), [(0, 1.0), (1, 0.0), (1, -5.0)])
def test_invalid_configuration_rejected(threshold, cooldown):
    with pytest.raises(ValueError):
        CircuitBreaker("x", failure_threshold=threshold, cooldown_s=cooldown)


def test_registry_shares_breakers_by_key():
    registry = CircuitBreakerRegistry()
    a = registry.get_or_create("openai", failure_threshold=2, cooldown_s=5.0)
    b = registry.get_or_create("openai")
    c = registry.get_or_create("gemini")
    assert a is b
    assert a is not c
    registry.reset()
    assert registry.get_or_create("openai") is not a
