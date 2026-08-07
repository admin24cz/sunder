"""Domain vocabulary shared across the sync service.

Kept in one module so the database layer, the Garmin client and the runner all
name the same states the same way, and so a value that has to match a database
CHECK constraint has exactly one definition.
"""

from __future__ import annotations

from enum import StrEnum


class ConnectionStatus(StrEnum):
    """Lifecycle of a Garmin connection (spec section 7.3).

    The values match the CHECK constraint on `garmin_connections.status`. A
    `StrEnum` so a member serialises straight to the string the database wants,
    without a conversion step that could drift.
    """

    ACTIVE = "active"
    """Working normally."""

    AUTH_FAILED = "auth_failed"
    """The stored password no longer works — usually the user changed it.

    Skipped by the sync until the user re-links; retrying would burn attempts
    against Garmin's failed-login limit for no chance of success.
    """

    RATE_LIMITED = "rate_limited"
    """Garmin is throttling us. Temporary — retried on a later run."""

    DISABLED = "disabled"
    """Unlinked by the user or switched off by an administrator."""

    @property
    def is_syncable(self) -> bool:
        """Whether a run should attempt this connection.

        Spec 7.3: everything except `active` and `rate_limited` is skipped.
        `rate_limited` is included because the condition is expected to clear on
        its own, and the next scheduled run is the retry.
        """
        return self in {ConnectionStatus.ACTIVE, ConnectionStatus.RATE_LIMITED}


class ActivityType(StrEnum):
    """Supported activity types (spec 5.2), extensible by design."""

    RUNNING = "running"
    CYCLING = "cycling"
    SWIMMING = "swimming"
    OTHER = "other"
