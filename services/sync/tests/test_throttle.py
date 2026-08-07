"""Tests for request pacing and retry backoff (spec 7.2).

Nothing here sleeps for real: the clock and the sleep function are injected, so
the assertions are about the exact delays requested rather than about elapsed
wall time, and the suite stays fast enough to run on every commit.
"""

from __future__ import annotations

import pytest

from sunder_sync.garmin import (
    GarminAuthError,
    GarminRateLimitedError,
    GarminUnavailableError,
    RateLimiter,
    RetryPolicy,
)


class FakeClock:
    """A monotonic clock that only moves when a test says so."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------


def test_first_request_is_not_delayed() -> None:
    """Nothing has been asked of Garmin yet, so there is nothing to wait for."""
    clock = FakeClock()
    limiter = RateLimiter(min_interval_seconds=1.5, sleep=clock.sleep, monotonic=clock.monotonic)

    assert limiter.wait() == 0.0
    assert clock.slept == []


def test_back_to_back_requests_wait_the_full_interval() -> None:
    clock = FakeClock()
    limiter = RateLimiter(min_interval_seconds=1.5, sleep=clock.sleep, monotonic=clock.monotonic)

    limiter.wait()
    assert limiter.wait() == pytest.approx(1.5)
    assert clock.slept == [pytest.approx(1.5)]


def test_time_already_spent_working_counts_towards_the_interval() -> None:
    """Pacing is a floor on the gap, not an unconditional extra delay."""
    clock = FakeClock()
    limiter = RateLimiter(min_interval_seconds=1.5, sleep=clock.sleep, monotonic=clock.monotonic)

    limiter.wait()
    clock.advance(1.0)  # a request took a second
    assert limiter.wait() == pytest.approx(0.5)


def test_no_wait_when_the_interval_has_already_passed() -> None:
    clock = FakeClock()
    limiter = RateLimiter(min_interval_seconds=1.5, sleep=clock.sleep, monotonic=clock.monotonic)

    limiter.wait()
    clock.advance(10.0)
    assert limiter.wait() == 0.0
    assert clock.slept == []


def test_pacing_does_not_drift_over_many_requests() -> None:
    """Each gap is one interval — delays must not compound run over run."""
    clock = FakeClock()
    limiter = RateLimiter(min_interval_seconds=2.0, sleep=clock.sleep, monotonic=clock.monotonic)

    limiter.wait()
    for _ in range(10):
        limiter.wait()

    assert clock.slept == [pytest.approx(2.0)] * 10
    assert clock.now == pytest.approx(20.0)


def test_reset_clears_the_pacing_state() -> None:
    """Starting a new user should not inherit the previous one's timing."""
    clock = FakeClock()
    limiter = RateLimiter(min_interval_seconds=1.5, sleep=clock.sleep, monotonic=clock.monotonic)

    limiter.wait()
    limiter.reset()
    assert limiter.wait() == 0.0


# ---------------------------------------------------------------------------
# RetryPolicy
# ---------------------------------------------------------------------------


def _policy(clock: FakeClock, **kwargs: object) -> RetryPolicy:
    """Build a policy with jitter switched off so delays are exact."""
    return RetryPolicy(sleep=clock.sleep, jitter=lambda: 0.0, **kwargs)  # type: ignore[arg-type]


def test_delays_are_exponential() -> None:
    """Spec 7.2: 2 s, 4 s, 8 s. Three attempts means two waits."""
    clock = FakeClock()
    assert list(_policy(clock).delays()) == [pytest.approx(2.0), pytest.approx(4.0)]

    longer = _policy(clock, max_attempts=4)
    assert list(longer.delays()) == [pytest.approx(2.0), pytest.approx(4.0), pytest.approx(8.0)]


def test_jitter_stays_within_a_quarter_of_the_base_delay() -> None:
    """Enough to desynchronise concurrent retries, not enough to distort them."""
    clock = FakeClock()
    policy = RetryPolicy(sleep=clock.sleep, jitter=lambda: 1.0)
    assert list(policy.delays()) == [pytest.approx(2.5), pytest.approx(5.0)]


def test_successful_call_does_not_sleep() -> None:
    clock = FakeClock()
    result = _policy(clock).call(lambda: "ok", description="test")

    assert result == "ok"
    assert clock.slept == []


def test_transient_failure_is_retried_and_can_succeed() -> None:
    clock = FakeClock()
    attempts = 0

    def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise GarminUnavailableError("boom")
        return "ok"

    assert _policy(clock).call(flaky, description="test") == "ok"
    assert attempts == 3
    assert clock.slept == [pytest.approx(2.0), pytest.approx(4.0)]


def test_transient_failure_gives_up_after_the_attempt_limit() -> None:
    clock = FakeClock()
    attempts = 0

    def always_failing() -> str:
        nonlocal attempts
        attempts += 1
        raise GarminUnavailableError("boom")

    with pytest.raises(GarminUnavailableError):
        _policy(clock).call(always_failing, description="test")

    assert attempts == 3, "should make exactly max_attempts attempts"
    assert clock.slept == [pytest.approx(2.0), pytest.approx(4.0)]


def test_rate_limit_is_not_retried() -> None:
    """Spec 7.2: on a rate limit, stop at once — retrying escalates it."""
    clock = FakeClock()
    attempts = 0

    def limited() -> str:
        nonlocal attempts
        attempts += 1
        raise GarminRateLimitedError("429")

    with pytest.raises(GarminRateLimitedError):
        _policy(clock).call(limited, description="test")

    assert attempts == 1
    assert clock.slept == []


def test_auth_failure_is_not_retried() -> None:
    """Repeating a rejected password walks the account towards a lockout."""
    clock = FakeClock()
    attempts = 0

    def rejected() -> str:
        nonlocal attempts
        attempts += 1
        raise GarminAuthError("bad password")

    with pytest.raises(GarminAuthError):
        _policy(clock).call(rejected, description="test")

    assert attempts == 1
    assert clock.slept == []


def test_the_specific_error_type_survives_the_retry_loop() -> None:
    """The caller needs the subclass to know which connection status to record."""
    clock = FakeClock()

    def failing() -> str:
        raise GarminUnavailableError("boom")

    with pytest.raises(GarminUnavailableError) as excinfo:
        _policy(clock).call(failing, description="test")
    assert excinfo.value.retryable is True


def test_a_single_attempt_policy_never_sleeps() -> None:
    clock = FakeClock()

    def failing() -> str:
        raise GarminUnavailableError("boom")

    with pytest.raises(GarminUnavailableError):
        _policy(clock, max_attempts=1).call(failing, description="test")
    assert clock.slept == []


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"max_attempts": 0}, "would never call"),
        ({"base_delay_seconds": -1.0}, "negative delay"),
    ],
)
def test_nonsensical_policies_are_rejected(kwargs: dict[str, object], reason: str) -> None:
    with pytest.raises(ValueError, match=r"must"):
        RetryPolicy(**kwargs)  # type: ignore[arg-type]
    assert reason
