"""Database access for the sync service (spec sections 7.4, 8.1, 8.2).

Talks to Supabase over PostgREST with the service role key, which bypasses RLS.
That is correct here and nowhere else: the sync service writes rows on behalf of
users who are not present to authenticate, and it is the only component holding
that key (spec 6.3).

Uses `httpx` directly rather than the `supabase` SDK. The operations needed here
are a handful of REST calls, and going direct buys precise control over the two
things that actually matter — the `Prefer` headers that make inserts idempotent,
and the exact error surface — without a client library's abstraction in between.
"""

from __future__ import annotations

import gzip
import json
import logging
from datetime import UTC, datetime
from typing import Any, Self

import httpx

from sunder_sync.domain import ActivityType, ConnectionStatus
from sunder_sync.models import GarminConnection, ParsedActivity
from sunder_sync.parsers import to_wkt_linestring

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30.0
STREAM_BUCKET = "activity-streams"


class DatabaseError(RuntimeError):
    """A Supabase request failed.

    Messages carry the status code and the operation, never the request body —
    a `garmin_connections` write body contains an encrypted credential, and a
    response body can echo it back.
    """


def encode_bytea(value: bytes) -> str:
    """Encode bytes for a PostgREST `bytea` column (Postgres hex format)."""
    return "\\x" + value.hex()


def decode_bytea(value: str) -> bytes:
    r"""Decode a `bytea` column as returned by PostgREST.

    Raises:
        ValueError: If the value is not the expected `\x`-prefixed hex.
    """
    return bytes.fromhex(value.removeprefix("\\x"))


class SyncRepository:
    """Every database operation the sync service performs.

    Use as a context manager so the underlying connection pool is closed:

        with SyncRepository(url=..., service_role_key=...) as repo:
            ...
    """

    def __init__(
        self,
        *,
        url: str,
        service_role_key: str,
        client: httpx.Client | None = None,
    ) -> None:
        """Build a repository.

        Args:
            url: Supabase project URL.
            service_role_key: The service role key. Sent in both `apikey` and
                `Authorization`, which is what the API gateway requires — a
                request carrying only `apikey` is rejected before PostgREST is
                ever reached.
            client: Injectable HTTP client, so tests can supply a mock transport
                and never touch the network.
        """
        self._base_url = url.rstrip("/")
        self._client = client or httpx.Client(
            base_url=self._base_url,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            headers={
                "apikey": service_role_key,
                "Authorization": f"Bearer {service_role_key}",
                "Content-Type": "application/json",
            },
        )

    def __enter__(self) -> Self:
        """Return self, for use as a context manager."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the underlying HTTP client."""
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    # -- internals ----------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        # `Any` is honest here: this is a pass-through to httpx.Client.request,
        # whose keyword arguments are a wide union (json, params, headers,
        # content, ...). Narrowing it would mean restating that union and
        # keeping it in sync for no safety gained at any call site.
        **kwargs: Any,  # noqa: ANN401
    ) -> httpx.Response:
        """Make a request and turn a failure into a `DatabaseError`.

        Args:
            method: HTTP verb.
            path: Path relative to the Supabase project URL.
            operation: Human-readable label for the error message. Must not
                contain credentials — it reaches the log and `sync_runs.errors`.
            **kwargs: Forwarded to `httpx.Client.request` unchanged.
        """
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise DatabaseError(f"{operation} failed: {type(exc).__name__}") from exc

        if response.status_code >= 400:
            raise DatabaseError(f"{operation} failed with HTTP {response.status_code}")
        return response

    # -- connections --------------------------------------------------------

    def list_syncable_connections(self) -> list[GarminConnection]:
        """Return the connections a run should attempt.

        Spec 7.3: everything except `active` and `rate_limited` is skipped. The
        filter is applied in the database rather than in Python so a growing
        table of disabled accounts costs nothing to skip.
        """
        syncable = [s.value for s in ConnectionStatus if s.is_syncable]
        response = self._request(
            "GET",
            "/rest/v1/garmin_connections",
            operation="listing connections",
            params={
                "select": (
                    "user_id,garmin_email,garmin_tokens_encrypted,"
                    "garmin_password_encrypted,status,last_sync_at"
                ),
                "status": f"in.({','.join(syncable)})",
            },
        )

        connections: list[GarminConnection] = []
        for row in response.json():
            connections.append(
                GarminConnection(
                    user_id=row["user_id"],
                    garmin_email=row["garmin_email"],
                    encrypted_tokens=_decode_optional_bytea(row.get("garmin_tokens_encrypted")),
                    encrypted_password=_decode_optional_bytea(row.get("garmin_password_encrypted")),
                    status=ConnectionStatus(row["status"]),
                    last_sync_at=_parse_optional_timestamp(row.get("last_sync_at")),
                )
            )
        return connections

    def store_tokens(self, user_id: str, sealed_tokens: bytes) -> None:
        """Save sealed session tokens and drop the stored password.

        The password is cleared in the same write, not left behind. Once tokens
        exist it is dead weight, and a password is the thing worth stealing —
        keeping one "just in case" is how a credential outlives its purpose
        (ADR 0003).
        """
        self._request(
            "PATCH",
            "/rest/v1/garmin_connections",
            operation="storing session tokens",
            params={"user_id": f"eq.{user_id}"},
            json={
                "garmin_tokens_encrypted": encode_bytea(sealed_tokens),
                "garmin_password_encrypted": None,
                "status": ConnectionStatus.ACTIVE.value,
                "last_error": None,
            },
        )

    def update_connection(
        self,
        user_id: str,
        *,
        status: ConnectionStatus,
        last_error: str | None = None,
        synced_at: datetime | None = None,
    ) -> None:
        """Record the outcome of a sync attempt for one user.

        Args:
            user_id: Whose connection to update.
            status: The new status.
            last_error: A short description, or None to clear a previous one.
                Never a credential or a raw response body.
            synced_at: When the successful sync finished. Left untouched when
                None, so a failed run does not erase the record of the last
                successful one — which is what the UI shows (spec 7.5).
        """
        payload: dict[str, Any] = {"status": status.value, "last_error": last_error}
        if synced_at is not None:
            payload["last_sync_at"] = synced_at.astimezone(UTC).isoformat()

        self._request(
            "PATCH",
            "/rest/v1/garmin_connections",
            operation="updating connection status",
            params={"user_id": f"eq.{user_id}"},
            json=payload,
        )

    # -- activities ---------------------------------------------------------

    def existing_activity_ids(self, user_id: str, candidate_ids: list[int]) -> set[int]:
        """Return which of `candidate_ids` this user already has.

        Used to decide which activities need their expensive detail request, and
        to report a truthful count of what was actually new. It is not what makes
        the import safe — the unique constraint does that — so a race between two
        runs costs a wasted insert, not a duplicate row.
        """
        if not candidate_ids:
            return set()

        response = self._request(
            "GET",
            "/rest/v1/activities",
            operation="checking for existing activities",
            params={
                "select": "garmin_activity_id",
                "user_id": f"eq.{user_id}",
                "garmin_activity_id": f"in.({','.join(str(i) for i in candidate_ids)})",
            },
        )
        return {int(row["garmin_activity_id"]) for row in response.json()}

    def insert_activities(self, user_id: str, activities: list[ParsedActivity]) -> int:
        """Insert activities, ignoring any that are already stored.

        Idempotence (spec 5.2) is enforced by the database, not by this method's
        bookkeeping: `resolution=ignore-duplicates` against the unique constraint
        on (user_id, garmin_activity_id) means a re-run, a concurrent run, or a
        bug in the caller all fail to produce a duplicate.

        Returns:
            How many rows were actually inserted — duplicates are not counted, so
            a second run of the same data reports zero.
        """
        if not activities:
            return 0

        rows = [_activity_to_row(user_id, activity) for activity in activities]
        response = self._request(
            "POST",
            "/rest/v1/activities",
            operation="inserting activities",
            params={"on_conflict": "user_id,garmin_activity_id"},
            headers={"Prefer": "resolution=ignore-duplicates,return=representation"},
            json=rows,
        )

        inserted = len(response.json())
        if inserted != len(rows):
            logger.info("Skipped %d activity/activities already present", len(rows) - inserted)
        return inserted

    def set_stream_path(self, user_id: str, garmin_activity_id: int, path: str) -> None:
        """Point an activity row at its uploaded stream object."""
        self._request(
            "PATCH",
            "/rest/v1/activities",
            operation="setting stream path",
            params={
                "user_id": f"eq.{user_id}",
                "garmin_activity_id": f"eq.{garmin_activity_id}",
            },
            json={"stream_path": path},
        )

    # -- storage ------------------------------------------------------------

    def upload_stream(self, user_id: str, garmin_activity_id: int, stream: dict[str, Any]) -> str:
        """Store the detailed per-second stream in Supabase Storage.

        Spec 8.2: the payload is far too large for the 500 MB database, so it
        lives in Storage and the row keeps only a path.

        The path is `<user_id>/<activity_id>.json.gz`, which is what the Storage
        RLS policy matches on — the owner is the first path segment, so
        authorisation is a string comparison rather than a join back into
        `activities` on every object read.

        Returns:
            The object path, to be stored in `activities.stream_path`.
        """
        path = f"{user_id}/{garmin_activity_id}.json.gz"
        body = gzip.compress(json.dumps(stream, separators=(",", ":")).encode("utf-8"))

        self._request(
            "POST",
            f"/storage/v1/object/{STREAM_BUCKET}/{path}",
            operation="uploading activity stream",
            headers={
                "Content-Type": "application/gzip",
                # Makes re-uploading the same activity safe, which matters
                # whenever a run is retried after a partial failure.
                "x-upsert": "true",
            },
            content=body,
        )
        return path

    # -- sync runs ----------------------------------------------------------

    def start_sync_run(self) -> str:
        """Open a `sync_runs` row and return its id (spec 7.4)."""
        response = self._request(
            "POST",
            "/rest/v1/sync_runs",
            operation="starting sync run",
            headers={"Prefer": "return=representation"},
            json={"started_at": datetime.now(UTC).isoformat()},
        )
        run_id: str = response.json()[0]["id"]
        return run_id

    def finish_sync_run(
        self,
        run_id: str,
        *,
        users_processed: int,
        activities_imported: int,
        errors: list[dict[str, str]],
    ) -> None:
        """Close a `sync_runs` row.

        Spec 7.4: this is what makes a failing sync diagnosable without reading
        GitHub Actions logs. `errors` holds one entry per failed user, so one
        broken account does not hide the rest (spec 7.1).
        """
        self._request(
            "PATCH",
            "/rest/v1/sync_runs",
            operation="finishing sync run",
            params={"id": f"eq.{run_id}"},
            json={
                "finished_at": datetime.now(UTC).isoformat(),
                "users_processed": users_processed,
                "activities_imported": activities_imported,
                "errors": errors,
            },
        )


def _decode_optional_bytea(value: object) -> bytes | None:
    """Decode a nullable `bytea` column.

    A connection now carries tokens, a password, or both, so either column can
    legitimately come back null.
    """
    if not isinstance(value, str) or not value:
        return None
    return decode_bytea(value)


def _parse_optional_timestamp(value: object) -> datetime | None:
    """Parse a PostgREST timestamptz, tolerating null."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Ignoring unparseable timestamp from the database")
        return None


def _activity_to_row(user_id: str, activity: ParsedActivity) -> dict[str, Any]:
    """Render a parsed activity as a PostgREST row."""
    row: dict[str, Any] = {
        "user_id": user_id,
        "garmin_activity_id": activity.garmin_activity_id,
        "type": activity.activity_type.value,
        "started_at": activity.started_at.astimezone(UTC).isoformat(),
        "duration_seconds": activity.duration_seconds,
        "distance_meters": activity.distance_meters,
        "elevation_gain_meters": activity.elevation_gain_meters,
        "avg_heart_rate": activity.avg_heart_rate,
        "max_heart_rate": activity.max_heart_rate,
        "avg_pace_seconds_per_km": activity.avg_pace_seconds_per_km,
    }

    # Omitted rather than sent as null when there is no usable track, so the
    # column keeps its NULL and PostGIS is never handed a degenerate LineString.
    if activity.has_track:
        row["track"] = f"SRID=4326;{to_wkt_linestring(activity.track)}"

    return row


__all__ = [
    "STREAM_BUCKET",
    "ActivityType",
    "DatabaseError",
    "SyncRepository",
    "decode_bytea",
    "encode_bytea",
]
