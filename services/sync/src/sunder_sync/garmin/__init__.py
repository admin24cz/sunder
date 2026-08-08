"""Garmin Connect access — pacing, backoff and per-user isolation (spec 7)."""

from sunder_sync.garmin.client import (
    DEFAULT_PAGE_SIZE,
    GarminApi,
    GarminApiFactory,
    GarminClient,
    classify_exception,
)
from sunder_sync.garmin.errors import (
    GarminAuthError,
    GarminError,
    GarminMfaRequiredError,
    GarminRateLimitedError,
    GarminResponseError,
    GarminUnavailableError,
)
from sunder_sync.garmin.throttle import (
    DEFAULT_BASE_DELAY_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MIN_INTERVAL_SECONDS,
    RateLimiter,
    RetryPolicy,
)

__all__ = [
    "DEFAULT_BASE_DELAY_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MIN_INTERVAL_SECONDS",
    "DEFAULT_PAGE_SIZE",
    "GarminApi",
    "GarminApiFactory",
    "GarminAuthError",
    "GarminClient",
    "GarminError",
    "GarminMfaRequiredError",
    "GarminRateLimitedError",
    "GarminResponseError",
    "GarminUnavailableError",
    "RateLimiter",
    "RetryPolicy",
    "classify_exception",
]
