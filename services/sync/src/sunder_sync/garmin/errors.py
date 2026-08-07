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
