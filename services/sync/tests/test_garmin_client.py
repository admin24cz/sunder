"""Tests for the Garmin client (spec 7.1, 7.2, 11.2).

Garmin is never contacted. The client talks to a fake through the `GarminApi`
protocol, which is what makes the retry, pacing and error-classification paths
testable at all — and what spec 11.2 requires.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import pytest

from sunder_sync.crypto import Secret
from sunder_sync.domain import ConnectionStatus
from sunder_sync.garmin import (
    GarminApi,
    GarminAuthError,
    GarminClient,
    GarminMfaRequiredError,
    GarminRateLimitedError,
    GarminResponseError,
    GarminUnavailableError,
    RateLimiter,
    RetryPolicy,
    classify_exception,
)
from tests.test_throttle import FakeClock

EMAIL = "runner@example.com"
PASSWORD = "garmin-password"


class FakeGarminApi:
    """Stand-in for `garminconnect.Garmin`."""

    def __init__(
        self,
        *,
        login_error: Exception | None = None,
        activities_error: Exception | None = None,
        activities: Sequence[dict[str, Any]] | None = None,
    ) -> None:
        self.login_error = login_error
        self.activities_error = activities_error
        self.activities = activities if activities is not None else []
        self.login_calls = 0
        #: What `login` was given each time — None for a password login, the
        #: token string when a session was resumed.
        self.tokenstores: list[str | None] = []
        self.list_calls: list[tuple[int, int]] = []

    def login(self, tokenstore: str | None = None) -> object:
        self.login_calls += 1
        self.tokenstores.append(tokenstore)
        if self.login_error is not None:
            raise self.login_error
        return object()

    def get_activities(self, start: int, limit: int) -> Sequence[dict[str, Any]]:
        self.list_calls.append((start, limit))
        if self.activities_error is not None:
            raise self.activities_error
        return self.activities

    def get_activity_details(self, activity_id: int) -> dict[str, Any]:
        return {"activityId": activity_id}


class FakeHttpError(Exception):
    """An exception carrying an HTTP status, like the real library's."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def build_client(
    api: FakeGarminApi, clock: FakeClock | None = None
) -> tuple[GarminClient, FakeClock, list[tuple[str | None, str | None]]]:
    """Build a client wired to `api`, with a fake clock and a factory spy."""
    clock = clock if clock is not None else FakeClock()
    factory_calls: list[tuple[str | None, str | None]] = []

    def factory(email: str | None, password: str | None) -> GarminApi:
        factory_calls.append((email, password))
        return api

    client = GarminClient(
        api_factory=factory,
        rate_limiter=RateLimiter(
            min_interval_seconds=1.5, sleep=clock.sleep, monotonic=clock.monotonic
        ),
        retry_policy=RetryPolicy(sleep=clock.sleep, jitter=lambda: 0.0),
    )
    return client, clock, factory_calls


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected", "expected_state"),
    [
        (429, GarminRateLimitedError, ConnectionStatus.RATE_LIMITED),
        (401, GarminAuthError, ConnectionStatus.AUTH_FAILED),
        (403, GarminAuthError, ConnectionStatus.AUTH_FAILED),
        (500, GarminUnavailableError, ConnectionStatus.ACTIVE),
        (503, GarminUnavailableError, ConnectionStatus.ACTIVE),
        (418, GarminResponseError, ConnectionStatus.ACTIVE),
    ],
)
def test_http_status_determines_the_outcome(
    status: int, expected: type[Exception], expected_state: ConnectionStatus
) -> None:
    error = classify_exception(FakeHttpError(status))
    assert isinstance(error, expected)
    assert error.connection_status is expected_state


def test_status_is_found_through_a_wrapped_response() -> None:
    """The real chain nests the response one or two levels down."""

    class Response:
        status_code = 429

    class WrappedError(Exception):
        response = Response()

    assert isinstance(classify_exception(WrappedError()), GarminRateLimitedError)


def test_status_is_found_through_a_chained_cause() -> None:
    inner = FakeHttpError(429)
    outer = Exception("wrapped")
    outer.__cause__ = inner
    assert isinstance(classify_exception(outer), GarminRateLimitedError)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("GarminConnectTooManyRequestsError", GarminRateLimitedError),
        ("GarminConnectAuthenticationError", GarminAuthError),
        ("GarminConnectConnectionError", GarminUnavailableError),
        ("SomethingEntirelyNew", GarminResponseError),
    ],
)
def test_exception_type_name_classifies_when_no_status_is_available(
    name: str, expected: type[Exception]
) -> None:
    """Matched by name so a library reorganising its exceptions still works."""
    error = classify_exception(type(name, (Exception,), {})())
    assert isinstance(error, expected)


@pytest.mark.parametrize("exc", [TimeoutError(), ConnectionError()])
def test_builtin_network_errors_are_transient(exc: Exception) -> None:
    assert isinstance(classify_exception(exc), GarminUnavailableError)


@pytest.mark.parametrize(
    "message",
    [
        # The exact text seen in production: garminconnect logs the 429 for each
        # login transport it tries, then raises an authentication error.
        "Mobile login returned 429 — IP rate limited by Garmin",
        "429 Too Many Requests",
        "Rate limit exceeded",
        "ratelimited",
    ],
)
def test_a_rate_limit_in_the_message_is_not_mistaken_for_bad_credentials(
    message: str,
) -> None:
    """The failure that marked a working account auth_failed in production.

    garminconnect raises `GarminConnectAuthenticationError` after every login
    transport received a 429, with the status code discarded. Classifying on the
    type name alone made a temporary IP throttle look like a wrong password —
    and spec 7.3 skips an auth_failed connection until the user re-links, so a
    transient block permanently disabled the sync.
    """
    exc = type("GarminConnectAuthenticationError", (Exception,), {})(message)
    error = classify_exception(exc)
    assert isinstance(error, GarminRateLimitedError)
    assert error.connection_status is ConnectionStatus.RATE_LIMITED


def test_a_rate_limit_is_found_through_a_chained_cause() -> None:
    inner = Exception("Mobile login returned 429 — IP rate limited by Garmin")
    outer = type("GarminConnectAuthenticationError", (Exception,), {})("login failed")
    outer.__cause__ = inner
    assert isinstance(classify_exception(outer), GarminRateLimitedError)


def test_a_genuine_auth_failure_is_still_reported_as_one() -> None:
    """The fix must not turn every failure into a rate limit."""
    exc = type("GarminConnectAuthenticationError", (Exception,), {})("Invalid credentials")
    error = classify_exception(exc)
    assert isinstance(error, GarminAuthError)
    assert error.connection_status is ConnectionStatus.AUTH_FAILED


def test_message_matching_does_not_loop_on_a_self_referential_chain() -> None:
    """A cause cycle must not hang the classifier."""
    a = Exception("outer")
    b = Exception("inner")
    a.__cause__ = b
    b.__cause__ = a
    assert classify_exception(a) is not None


def test_a_rate_limit_wins_over_the_type_name() -> None:
    """A 429 delivered as a connection error must still stop the run."""
    exc = type("GarminConnectConnectionError", (Exception,), {})()
    exc.status_code = 429
    assert isinstance(classify_exception(exc), GarminRateLimitedError)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def test_login_passes_the_revealed_password_to_the_factory_only() -> None:
    api = FakeGarminApi()
    client, _, factory_calls = build_client(api)

    client.login(EMAIL, Secret(PASSWORD))

    assert factory_calls == [(EMAIL, PASSWORD)]
    assert api.login_calls == 1
    assert client.is_authenticated


def test_login_never_writes_the_password_to_the_log(caplog: pytest.LogCaptureFixture) -> None:
    """Spec 6.4, on the one path where the plaintext genuinely exists."""
    api = FakeGarminApi()
    client, _, _ = build_client(api)

    with caplog.at_level(logging.DEBUG):
        client.login(EMAIL, Secret(PASSWORD))

    assert PASSWORD not in caplog.text
    assert EMAIL in caplog.text


def test_failed_login_never_leaks_the_password_into_the_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The failure path is where a password most plausibly escapes."""
    api = FakeGarminApi(login_error=FakeHttpError(401))
    client, _, _ = build_client(api)

    with caplog.at_level(logging.DEBUG), pytest.raises(GarminAuthError) as excinfo:
        client.login(EMAIL, Secret(PASSWORD))

    assert PASSWORD not in str(excinfo.value)
    assert PASSWORD not in caplog.text
    # The traceback must not carry a frame holding the plaintext either.
    assert excinfo.value.__cause__ is None


def test_rejected_credentials_are_not_retried() -> None:
    api = FakeGarminApi(login_error=FakeHttpError(401))
    client, clock, _ = build_client(api)

    with pytest.raises(GarminAuthError) as excinfo:
        client.login(EMAIL, Secret(PASSWORD))

    assert api.login_calls == 1
    assert clock.slept == [], "a rejected password must not be retried"
    assert excinfo.value.connection_status is ConnectionStatus.AUTH_FAILED


def test_rate_limited_login_stops_immediately() -> None:
    api = FakeGarminApi(login_error=FakeHttpError(429))
    client, clock, _ = build_client(api)

    with pytest.raises(GarminRateLimitedError) as excinfo:
        client.login(EMAIL, Secret(PASSWORD))

    assert api.login_calls == 1
    assert clock.slept == []
    assert excinfo.value.connection_status is ConnectionStatus.RATE_LIMITED


def test_transient_login_failure_is_retried_with_backoff() -> None:
    api = FakeGarminApi(login_error=FakeHttpError(503))
    client, clock, _ = build_client(api)

    with pytest.raises(GarminUnavailableError):
        client.login(EMAIL, Secret(PASSWORD))

    assert api.login_calls == 3
    assert clock.slept == [pytest.approx(2.0), pytest.approx(4.0)]


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


def test_requests_before_login_are_a_programming_error() -> None:
    """Not a GarminError: this is a bug in the runner, not the user's problem.

    Recording it against their connection status would blame the wrong thing.
    """
    api = FakeGarminApi()
    client, _, _ = build_client(api)

    with pytest.raises(RuntimeError, match="login"):
        client.list_activities()


def test_activities_are_returned_unparsed() -> None:
    """Parsing belongs to sunder_sync.parsers, so a schema change surfaces there."""
    payload = [{"activityId": 1}, {"activityId": 2}]
    api = FakeGarminApi(activities=payload)
    client, _, _ = build_client(api)
    client.login(EMAIL, Secret(PASSWORD))

    assert client.list_activities(start=0, limit=20) == payload
    assert api.list_calls == [(0, 20)]


def test_every_request_is_paced() -> None:
    """Spec 7.2: a minimum gap between calls, login included."""
    api = FakeGarminApi(activities=[])
    client, clock, _ = build_client(api)

    client.login(EMAIL, Secret(PASSWORD))
    client.list_activities()
    client.list_activities(start=20)

    assert clock.slept == [pytest.approx(1.5), pytest.approx(1.5)]


def test_rate_limited_listing_stops_immediately() -> None:
    api = FakeGarminApi(activities_error=FakeHttpError(429))
    client, clock, _ = build_client(api)
    client.login(EMAIL, Secret(PASSWORD))
    clock.slept.clear()

    with pytest.raises(GarminRateLimitedError):
        client.list_activities()

    assert len(api.list_calls) == 1
    # The pacing wait still happened; no *backoff* sleep was added.
    assert clock.slept == [pytest.approx(1.5)]


# ---------------------------------------------------------------------------
# Second-factor prompts (found on the first real Garmin login)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        # The exact text Garmin produced on the first real login attempt.
        "MFA Required but no prompt_mfa mechanism supplied",
        "Two-factor authentication required",
        "2FA code needed",
        "multi-factor challenge",
    ],
)
def test_a_second_factor_prompt_is_not_reported_as_bad_credentials(message: str) -> None:
    """The password was accepted; a code was demanded.

    Reporting this as rejected credentials sends the user to change a password
    that works, and re-linking the account fails in exactly the same way.
    """
    exc = type("GarminConnectAuthenticationError", (Exception,), {})(message)
    error = classify_exception(exc)
    assert isinstance(error, GarminMfaRequiredError)
    assert not error.retryable


def test_a_second_factor_prompt_is_found_through_an_inner_exception_class() -> None:
    """Garminconnect signals it with an inner `_MFARequired` carrying no message."""
    inner = type("_MFARequired", (Exception,), {})("")
    outer = type("GarminConnectAuthenticationError", (Exception,), {})("login failed")
    outer.__cause__ = inner
    assert isinstance(classify_exception(outer), GarminMfaRequiredError)


def test_a_plain_auth_failure_is_still_not_an_mfa_prompt() -> None:
    exc = type("GarminConnectAuthenticationError", (Exception,), {})("Invalid credentials")
    assert isinstance(classify_exception(exc), GarminAuthError)
