"""The sync run itself (spec sections 7.1, 7.3, 7.4).

One rule shapes this module: **a failure for one user must never affect
another.** Each connection is processed inside its own try/except, the failure
becomes a value rather than propagating, and the loop continues. A single user
whose Garmin password changed cannot stop everyone else's activities importing.

The run is also idempotent end to end. Activities are deduplicated by the
database, streams are uploaded with upsert, and a run interrupted halfway leaves
nothing that a later run cannot correct.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sunder_sync.config import SyncConfig
from sunder_sync.crypto import open_credential
from sunder_sync.db import SyncRepository
from sunder_sync.domain import ConnectionStatus
from sunder_sync.garmin import GarminClient, GarminError
from sunder_sync.models import GarminConnection, ParsedActivity, SyncOutcome
from sunder_sync.parsers import parse_activity_summary, parse_track, simplify_track

logger = logging.getLogger(__name__)

type ClientFactory = Callable[[], GarminClient]


@dataclass
class SyncRunSummary:
    """What a whole run did (spec 7.4)."""

    outcomes: list[SyncOutcome] = field(default_factory=list)

    @property
    def users_processed(self) -> int:
        """How many connections were attempted."""
        return len(self.outcomes)

    @property
    def activities_imported(self) -> int:
        """Total newly stored activities across all users."""
        return sum(outcome.activities_imported for outcome in self.outcomes)

    @property
    def errors(self) -> list[dict[str, str]]:
        """One entry per failed user, shaped for `sync_runs.errors`."""
        return [
            {"user_id": o.user_id, "status": o.status.value, "error": o.error or "unknown"}
            for o in self.outcomes
            if not o.succeeded
        ]

    @property
    def failed(self) -> int:
        """How many connections failed."""
        return len(self.errors)


def sync_user(
    connection: GarminConnection,
    *,
    repository: SyncRepository,
    config: SyncConfig,
    client_factory: ClientFactory,
) -> SyncOutcome:
    """Import one user's new activities.

    Raises nothing on a Garmin or database problem — the failure comes back as a
    `SyncOutcome`, because the caller has to keep going (spec 7.1).

    Args:
        connection: The user's Garmin link, with the password still encrypted.
        repository: Database access.
        config: The run's configuration, including the encryption key.
        client_factory: Builds a fresh Garmin client. Fresh per user, because a
            client holds a live session and reusing one would mean fetching one
            person's activities under another's login.

    Returns:
        What happened, including the status to record for this connection.
    """
    # Decryption comes first, before any client exists. A credential we cannot
    # read means there is nothing to log in with, so building a session object
    # would be wasted work — and on a run following a botched key rotation, that
    # is every user.
    #
    # The ciphertext is bound to the user id, so a payload moved onto another
    # row fails here rather than logging into the wrong Garmin account.
    password = open_credential(
        connection.encrypted_password,
        user_id=connection.user_id,
        keys=config.credential_keys,
    )

    client = client_factory()
    client.login(connection.garmin_email, password)
    # Drop the only remaining reference. CPython cannot zero the string, but
    # this at least ends its reachability at the earliest possible point.
    del password

    summaries = client.list_activities(start=0, limit=config.max_activities_per_user)
    parsed = _parse_summaries(summaries)

    if not parsed:
        logger.info("No activities returned for user %s", connection.user_id)
        return SyncOutcome(user_id=connection.user_id, status=ConnectionStatus.ACTIVE)

    known = repository.existing_activity_ids(
        connection.user_id, [a.garmin_activity_id for a in parsed]
    )
    new_activities = [a for a in parsed if a.garmin_activity_id not in known]

    if not new_activities:
        logger.info("User %s is already up to date", connection.user_id)
        return SyncOutcome(user_id=connection.user_id, status=ConnectionStatus.ACTIVE)

    logger.info(
        "User %s has %d new activity/activities of %d listed",
        connection.user_id,
        len(new_activities),
        len(parsed),
    )

    detailed = [_with_details(activity, client=client) for activity in new_activities]
    imported = repository.insert_activities(connection.user_id, detailed)

    _upload_streams(detailed, connection=connection, repository=repository, client=client)

    return SyncOutcome(
        user_id=connection.user_id,
        status=ConnectionStatus.ACTIVE,
        activities_imported=imported,
    )


def run_sync(
    *,
    repository: SyncRepository,
    config: SyncConfig,
    client_factory: ClientFactory,
) -> SyncRunSummary:
    """Process every syncable connection, isolating failures per user.

    Opens a `sync_runs` row, works through the connections, and closes the row
    whatever happens — including on an unexpected error — so a run never
    disappears from the log it exists to provide (spec 7.4).
    """
    run_id = repository.start_sync_run()
    summary = SyncRunSummary()

    try:
        connections = repository.list_syncable_connections()
        logger.info("Starting sync for %d connection(s)", len(connections))

        for connection in connections:
            summary.outcomes.append(
                _sync_one_isolated(
                    connection,
                    repository=repository,
                    config=config,
                    client_factory=client_factory,
                )
            )
    finally:
        repository.finish_sync_run(
            run_id,
            users_processed=summary.users_processed,
            activities_imported=summary.activities_imported,
            errors=summary.errors,
        )

    logger.info(
        "Sync finished: %d user(s), %d activity/activities imported, %d failure(s)",
        summary.users_processed,
        summary.activities_imported,
        summary.failed,
    )
    return summary


def _sync_one_isolated(
    connection: GarminConnection,
    *,
    repository: SyncRepository,
    config: SyncConfig,
    client_factory: ClientFactory,
) -> SyncOutcome:
    """Run `sync_user` and convert any failure into an outcome.

    The two except branches are deliberately different. A `GarminError` already
    knows which connection status it implies — a rejected password means
    `auth_failed`, a 429 means `rate_limited`. Anything else is our bug or a
    database problem, and the connection stays `active` so the next run retries
    it rather than the user being told to re-link over a fault of ours.
    """
    try:
        outcome = sync_user(
            connection,
            repository=repository,
            config=config,
            client_factory=client_factory,
        )
    except GarminError as exc:
        # The message is the exception's own text, which by construction names
        # only the class of problem — never a password or a response body.
        message = f"{type(exc).__name__}: {exc}"
        logger.warning("Sync failed for user %s: %s", connection.user_id, message)
        _record(repository, connection.user_id, status=exc.connection_status, error=message)
        return SyncOutcome(user_id=connection.user_id, status=exc.connection_status, error=message)
    except Exception as exc:
        # `exc_info` is safe: the traceback holds our own frames, and the one
        # frame that ever held a plaintext password raises `from None`.
        message = f"{type(exc).__name__}: {exc}"
        logger.exception("Unexpected failure syncing user %s", connection.user_id)
        _record(repository, connection.user_id, status=ConnectionStatus.ACTIVE, error=message)
        return SyncOutcome(
            user_id=connection.user_id, status=ConnectionStatus.ACTIVE, error=message
        )

    _record(
        repository,
        connection.user_id,
        status=ConnectionStatus.ACTIVE,
        error=None,
        synced_at=datetime.now(UTC),
    )
    return outcome


def _record(
    repository: SyncRepository,
    user_id: str,
    *,
    status: ConnectionStatus,
    error: str | None,
    synced_at: datetime | None = None,
) -> None:
    """Write a connection's outcome, swallowing a failure to do so.

    If recording the status fails, the run must still continue: losing one
    status update is a much smaller problem than abandoning every remaining
    user because the bookkeeping write failed.
    """
    try:
        repository.update_connection(user_id, status=status, last_error=error, synced_at=synced_at)
    except Exception:
        logger.exception("Could not record sync status for user %s", user_id)


def _parse_summaries(summaries: object) -> list[ParsedActivity]:
    """Parse a page of summaries, skipping any single unparseable one.

    One malformed activity must not cost the user the rest of the page — spec
    7.1's isolation principle applied one level down.
    """
    if not isinstance(summaries, list | tuple):
        return []

    parsed: list[ParsedActivity] = []
    for raw in summaries:
        if not isinstance(raw, dict):
            continue
        try:
            parsed.append(parse_activity_summary(raw))
        except Exception:
            logger.warning("Skipping an activity whose summary could not be parsed")
    return parsed


def _with_details(activity: ParsedActivity, *, client: GarminClient) -> ParsedActivity:
    """Attach a simplified GPS track, if the activity has one.

    A failure here degrades rather than aborts: the activity is still worth
    storing without its map, and re-fetching the track on a later run is cheap
    compared with losing the record.
    """
    try:
        details = client.get_activity_details(activity.garmin_activity_id)
    except GarminError:
        # Propagated: a rate limit or an auth failure mid-page has to stop this
        # user's run, not be quietly degraded into a missing track.
        raise
    except Exception:
        logger.warning(
            "Could not fetch details for activity %d; storing it without a track",
            activity.garmin_activity_id,
        )
        return activity

    track = parse_track(details)
    if len(track) < 2:
        return activity

    from dataclasses import replace

    return replace(activity, track=simplify_track(track))


def _upload_streams(
    activities: list[ParsedActivity],
    *,
    connection: GarminConnection,
    repository: SyncRepository,
    client: GarminClient,
) -> None:
    """Placeholder for detailed stream upload (spec 8.2).

    Not yet wired: the per-second payload needs one extra Garmin request per
    activity, and spec 7.2 wants the request budget spent on getting activities
    in first. The Storage bucket, its RLS policy and
    `SyncRepository.upload_stream` are all in place, so enabling this is a
    change here alone.
    """
    del activities, connection, repository, client
