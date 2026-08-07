"""Pacing and retry policy for Garmin requests (spec section 7.2).

Two independent concerns, kept separate because they answer different questions:

*   `RateLimiter` — *how fast may we call at all?* A floor on the gap between
    consecutive requests, applied whether or not anything failed.
*   `RetryPolicy` — *what do we do when a call fails?* Exponential backoff, and
    only for failures that could plausibly succeed on a second attempt.

Both take their clock and sleep function as parameters. That is what lets the
tests assert on the exact delays without spending real seconds waiting for them.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from sunder_sync.garmin.errors import GarminError

logger = logging.getLogger(__name__)

SleepFn = Callable[[float], None]
MonotonicFn = Callable[[], float]

DEFAULT_MIN_INTERVAL_SECONDS = 1.5
"""Spec 7.2 asks for at least 1–2 s between requests; 1.5 s sits in the middle.

The point is not to respect a published limit — Garmin publishes none for this
access path — but to look like a person using the app rather than a script.
"""

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 2.0
"""Backoff runs 2 s, 4 s, 8 s (spec 7.2)."""


@dataclass
class RateLimiter:
    """Enforces a minimum gap between consecutive requests.

    Stateful and not thread-safe. The sync processes users sequentially and
    deliberately so — parallel requests would defeat the purpose of pacing.
    """

    min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS
    sleep: SleepFn = time.sleep
    monotonic: MonotonicFn = time.monotonic

    _last_request_at: float | None = field(default=None, init=False, repr=False)

    def wait(self) -> float:
        """Block until the next request is allowed.

        Returns:
            How long this call actually slept, in seconds. Returned rather than
            discarded so callers and tests can observe the pacing.
        """
        now = self.monotonic()

        if self._last_request_at is None:
            # First request of the run: nothing to wait for.
            self._last_request_at = now
            return 0.0

        elapsed = now - self._last_request_at
        remaining = self.min_interval_seconds - elapsed

        if remaining > 0:
            self.sleep(remaining)
            # Derive from the intended schedule rather than reading the clock
            # again, so a slow sleep does not compound into ever-growing gaps.
            self._last_request_at = self._last_request_at + self.min_interval_seconds
            return remaining

        self._last_request_at = now
        return 0.0

    def reset(self) -> None:
        """Forget the last request time, e.g. when starting a new user."""
        self._last_request_at = None


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff for transient Garmin failures.

    Only `GarminError` subclasses with `retryable = True` are retried. A rate
    limit is explicitly *not* retryable: spec 7.2 requires stopping immediately
    rather than backing off, because continuing to knock is what escalates
    throttling into a block.
    """

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    base_delay_seconds: float = DEFAULT_BASE_DELAY_SECONDS
    sleep: SleepFn = time.sleep
    jitter: Callable[[], float] = random.random

    def __post_init__(self) -> None:
        """Reject a policy that would never make a call."""
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must not be negative")

    def delays(self) -> Iterator[float]:
        """Yield the delay before each retry, in order.

        With the defaults: 2 s, 4 s. (Three attempts means two waits.)

        Each delay carries up to 25% of added jitter. Without it, several users
        failing on the same Garmin hiccup would retry in lockstep and arrive as
        a burst — exactly the pattern the pacing above exists to avoid.
        """
        for attempt in range(self.max_attempts - 1):
            base = self.base_delay_seconds * (2**attempt)
            yield base * (1 + 0.25 * self.jitter())

    def call[T](self, operation: Callable[[], T], *, description: str) -> T:
        """Run `operation`, retrying transient failures with backoff.

        Args:
            operation: The call to make. Invoked up to `max_attempts` times.
            description: Short human-readable label used in log messages. Must
                not contain credentials — it ends up in the sync log.

        Returns:
            Whatever `operation` returns on its first successful attempt.

        Raises:
            GarminError: The last failure, once attempts are exhausted or the
                failure is not retryable. Re-raised as-is so the caller still
                sees the specific subclass and its connection status.
        """
        delays = self.delays()
        attempt = 0

        while True:
            attempt += 1
            try:
                return operation()
            except GarminError as exc:
                if not exc.retryable:
                    logger.warning(
                        "%s failed with a non-retryable error (%s); giving up",
                        description,
                        type(exc).__name__,
                    )
                    raise

                delay = next(delays, None)
                if delay is None:
                    logger.warning("%s failed after %d attempts; giving up", description, attempt)
                    raise

                logger.info(
                    "%s failed (%s); retrying in %.1f s (attempt %d of %d)",
                    description,
                    type(exc).__name__,
                    delay,
                    attempt + 1,
                    self.max_attempts,
                )
                self.sleep(delay)
