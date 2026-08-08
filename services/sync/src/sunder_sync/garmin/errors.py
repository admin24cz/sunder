"""Exceptions raised by the Garmin client.

The hierarchy exists to answer one question at the call site: *should the run
try this again?* Each class carries that answer, so the retry logic never has to
inspect a message or a status code.

No exception here ever carries a password, a token, or a response body that
might contain either (spec 6.4).
"""

from __future__ import annotations

from sunder_sync.domain import ConnectionStatus


class GarminError(Exception):
    """Base class for every Garmin access failure.

    Attributes:
        retryable: Whether repeating the same call could plausibly succeed.
        connection_status: The state to record for this user's connection.
    """

    retryable: bool = False
    connection_status: ConnectionStatus = ConnectionStatus.ACTIVE


class GarminAuthError(GarminError):
    """Garmin rejected the credentials.

    Not retryable, and deliberately so: repeating a login with a password Garmin
    has already refused spends attempts against the account's failed-login limit
    and moves it closer to a lockout. The user has to re-link.
    """

    retryable = False
    connection_status = ConnectionStatus.AUTH_FAILED


class GarminMfaRequiredError(GarminError):
    """Garmin accepted the password and then asked for a second factor.

    Distinct from `GarminAuthError` because the remedy is completely different
    and the wrong message is actively misleading. The stored password is
    correct; re-linking the account will fail in exactly the same way. Telling
    the user their credentials were rejected sends them to change a password
    that was never the problem.

    A headless sync has nowhere to obtain a one-time code, so this cannot be
    retried and the connection is parked until the user resolves it — either by
    switching that account to token-based access, or by turning the second
    factor off (see docs/security.md).
    """

    retryable = False
    connection_status = ConnectionStatus.AUTH_FAILED


class GarminRateLimitedError(GarminError):
    """Garmin is throttling or has blocked us (HTTP 429, or a block page).

    Not retryable *within this run*. Spec 7.2 is explicit: on detecting a limit,
    stop immediately for that user and make no further attempts. Backing off and
    trying again is what turns throttling into a block.
    """

    retryable = False
    connection_status = ConnectionStatus.RATE_LIMITED


class GarminUnavailableError(GarminError):
    """A transient failure — a timeout, a connection reset, a 5xx.

    Retryable with backoff. This is the only class the retry loop acts on.
    """

    retryable = True
    connection_status = ConnectionStatus.ACTIVE


class GarminResponseError(GarminError):
    """Garmin returned a response we could not make sense of.

    Not retryable: the same request will produce the same unparseable answer.
    Usually means Garmin changed a payload shape, which is the failure mode spec
    section 7.5 expects and wants surfaced rather than silently retried.
    """

    retryable = False
    connection_status = ConnectionStatus.ACTIVE
