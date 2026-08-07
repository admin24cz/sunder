"""Garmin Connect client with pacing, backoff and safe error handling.

This is the only module that talks to Garmin. It exists to keep three things in
one place:

*   **Pacing and retries**, so no call site can accidentally hammer Garmin.
*   **Error translation**, turning whatever the underlying library raises into
    the small vocabulary in `errors.py`, which says whether to retry and what
    connection status to record.
*   **Credential handling**, so the plaintext password is revealed at exactly
    one line and is never attached to an exception or a log record (spec 6.4).

The concrete library is behind the `GarminApi` protocol. That is not
speculative abstraction: spec section 7.5 names "Garmin changes the login flow
and `garth` stops working" as an expected event, and the tests are required
never to touch the network (spec 11.2).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from sunder_sync.crypto import Secret
from sunder_sync.garmin.errors import (
    GarminAuthError,
    GarminError,
    GarminRateLimitedError,
    GarminResponseError,
    GarminUnavailableError,
)
from sunder_sync.garmin.throttle import RateLimiter, RetryPolicy

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 20
"""Activities fetched per listing call.

Small on purpose. Each page is one paced request, and an incremental sync
normally needs one page; a large page size would only help the historical
backfill, which spec 7.2 wants spread across runs anyway.
"""


class GarminApi(Protocol):
    """The slice of the Garmin library this client actually uses."""

    def login(self) -> object:
        """Authenticate. Raises on bad credentials."""
        ...

    def get_activities(self, start: int, limit: int) -> Sequence[dict[str, Any]]:
        """Return a page of activity summaries, newest first."""
        ...

    def get_activity_details(self, activity_id: int) -> dict[str, Any]:
        """Return the detailed payload for one activity."""
        ...


type GarminApiFactory = Callable[[str, str], GarminApi]
"""Builds an API object from an email and a **plaintext** password.

Takes the revealed password because the underlying library needs it. Keep
implementations trivial — construct the object and return it, nothing else —
so the plaintext's lifetime stays as short as possible.
"""


def _status_code_of(exc: BaseException) -> int | None:
    """Dig an HTTP status code out of an exception, if it carries one.

    Written defensively rather than against one library's exception shape: the
    chain from `garminconnect` through `garth` to the HTTP layer has changed
    before, and a missed status code would mean treating a rate limit as a
    generic error and retrying straight into a block.
    """
    for candidate in (exc, getattr(exc, "error", None), getattr(exc, "__cause__", None)):
        if candidate is None:
            continue
        response = getattr(candidate, "response", None)
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            return status
        status = getattr(candidate, "status_code", None)
        if isinstance(status, int):
            return status
    return None


def classify_exception(exc: BaseException) -> GarminError:
    """Translate a library exception into this package's vocabulary.

    Classification order matters. The HTTP status is checked before the
    exception's type name, because a 429 delivered as a generic connection error
    still has to stop the run for that user rather than be retried.

    Args:
        exc: Whatever the Garmin library raised.

    Returns:
        A `GarminError` whose `retryable` and `connection_status` describe what
        the caller should do. The message names the class of problem only —
        never the response body, which can contain a session token.
    """
    status = _status_code_of(exc)
    if status is not None:
        if status == 429:
            return GarminRateLimitedError("Garmin rate limited the request (HTTP 429)")
        if status in (401, 403):
            return GarminAuthError(f"Garmin rejected the credentials (HTTP {status})")
        if status >= 500:
            return GarminUnavailableError(f"Garmin returned a server error (HTTP {status})")
        return GarminResponseError(f"Garmin returned an unexpected status (HTTP {status})")

    # No status available — fall back to the exception's type. Matched by name
    # so this keeps working if the library reorganises its exception module.
    name = type(exc).__name__
    if "TooManyRequests" in name or "RateLimit" in name:
        return GarminRateLimitedError("Garmin rate limited the request")
    if "Authentication" in name or "Login" in name:
        return GarminAuthError("Garmin rejected the credentials")
    if isinstance(exc, TimeoutError | ConnectionError) or "Connection" in name or "Timeout" in name:
        return GarminUnavailableError(f"Could not reach Garmin ({name})")

    return GarminResponseError(f"Unexpected failure talking to Garmin ({name})")


class GarminClient:
    """Paced, retrying access to one user's Garmin Connect account.

    One instance per user per run. It holds a live session, and reusing it
    across users would mean one person's activities being fetched under
    another's login.
    """

    def __init__(
        self,
        *,
        api_factory: GarminApiFactory,
        rate_limiter: RateLimiter | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        """Build a client.

        Args:
            api_factory: Constructs the underlying Garmin API object.
            rate_limiter: Pacing between requests. A fresh default is created
                per client, so one user's pacing never delays another's.
            retry_policy: Backoff for transient failures.
        """
        self._api_factory = api_factory
        self._rate_limiter = rate_limiter if rate_limiter is not None else RateLimiter()
        self._retry_policy = retry_policy if retry_policy is not None else RetryPolicy()
        self._api: GarminApi | None = None

    @property
    def is_authenticated(self) -> bool:
        """Whether `login` has completed successfully."""
        return self._api is not None

    def login(self, email: str, password: Secret) -> None:
        """Authenticate against Garmin Connect.

        Args:
            email: The Garmin account email.
            password: The plaintext password, wrapped. Revealed on exactly one
                line below and not bound to any longer-lived name.

        Raises:
            GarminAuthError: The credentials were rejected. Not retried — see
                the class docstring in `errors.py`.
            GarminRateLimitedError: Garmin is throttling; the run must stop for
                this user.
            GarminUnavailableError: Transient failure that survived every retry.
        """
        self._rate_limiter.wait()

        def attempt() -> GarminApi:
            try:
                # The single point at which the plaintext exists. Passed
                # straight into the factory; the local goes out of scope with
                # this frame, and no exception raised below can capture it,
                # because the reveal happens outside the try that logs.
                api = self._api_factory(email, password.reveal())
                api.login()
            except GarminError:
                raise
            except Exception as exc:
                raise classify_exception(exc) from None
            return api

        # `from None` above, and no exception chaining here: the original
        # traceback can hold the request that carried the password.
        self._api = self._retry_policy.call(attempt, description=f"Garmin login for {email}")
        logger.info("Garmin login succeeded for %s", email)

    def list_activities(
        self, *, start: int = 0, limit: int = DEFAULT_PAGE_SIZE
    ) -> Sequence[dict[str, Any]]:
        """Fetch a page of activity summaries, newest first.

        Args:
            start: Offset into the user's activity history.
            limit: Page size.

        Returns:
            Raw summary payloads, left unparsed — parsing belongs to
            `sunder_sync.parsers`, so a Garmin schema change surfaces there
            rather than here.

        Raises:
            GarminError: Classified per `classify_exception`.
            RuntimeError: If called before `login`.
        """
        api = self._require_session()
        self._rate_limiter.wait()

        def attempt() -> Sequence[dict[str, Any]]:
            try:
                return api.get_activities(start, limit)
            except GarminError:
                raise
            except Exception as exc:
                raise classify_exception(exc) from exc

        return self._retry_policy.call(
            attempt, description=f"listing activities [{start}:{start + limit}]"
        )

    def get_activity_details(self, activity_id: int) -> dict[str, Any]:
        """Fetch the detailed payload for one activity.

        Raises:
            GarminError: Classified per `classify_exception`.
            RuntimeError: If called before `login`.
        """
        api = self._require_session()
        self._rate_limiter.wait()

        def attempt() -> dict[str, Any]:
            try:
                return api.get_activity_details(activity_id)
            except GarminError:
                raise
            except Exception as exc:
                raise classify_exception(exc) from exc

        return self._retry_policy.call(attempt, description=f"activity {activity_id} details")

    def _require_session(self) -> GarminApi:
        """Return the live session, or fail loudly.

        A programming error rather than a `GarminError`: calling this without a
        login is a bug in the runner, not a problem with the user's connection,
        and must not be recorded against their account.
        """
        if self._api is None:
            raise RuntimeError("GarminClient.login() must be called before making requests")
        return self._api
