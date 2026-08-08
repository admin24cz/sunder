"""Tests for the database layer (spec 5.2, 7.3, 7.4, 8.2).

Supabase is never contacted. `httpx.MockTransport` intercepts every request, so
the tests can assert on exactly what would be sent — which is where the
interesting behaviour lives: the `Prefer` headers that make imports idempotent,
the status filter that implements spec 7.3, and the WKT axis order.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from sunder_sync.db import DatabaseError, SyncRepository, decode_bytea, encode_bytea
from sunder_sync.domain import ActivityType, ConnectionStatus
from sunder_sync.models import ParsedActivity, TrackPoint

SERVICE_KEY = "sb_secret_test_key"
USER_ID = "11111111-1111-4111-8111-111111111111"


class RecordingTransport(httpx.MockTransport):
    """Captures every request and replies with a queued response."""

    def __init__(self, responses: list[httpx.Response] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._responses = list(responses or [])
        super().__init__(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._responses:
            return self._responses.pop(0)
        return httpx.Response(200, json=[])

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]


def build_repo(
    responses: list[httpx.Response] | None = None,
) -> tuple[SyncRepository, RecordingTransport]:
    transport = RecordingTransport(responses)
    client = httpx.Client(
        base_url="https://project.supabase.co",
        transport=transport,
        headers={
            "apikey": SERVICE_KEY,
            "Authorization": f"Bearer {SERVICE_KEY}",
            "Content-Type": "application/json",
        },
    )
    return SyncRepository(
        url="https://project.supabase.co", service_role_key=SERVICE_KEY, client=client
    ), transport


def activity(**overrides: Any) -> ParsedActivity:
    defaults: dict[str, Any] = {
        "garmin_activity_id": 1001,
        "activity_type": ActivityType.RUNNING,
        "started_at": datetime(2026, 1, 15, 6, 30, tzinfo=UTC),
        "duration_seconds": 2535,
        "distance_meters": 10520.3,
    }
    defaults.update(overrides)
    return ParsedActivity(**defaults)


def body_of(request: httpx.Request) -> Any:
    return json.loads(request.content)


# ---------------------------------------------------------------------------
# bytea round-trip
# ---------------------------------------------------------------------------


def test_bytea_round_trips() -> None:
    payload = b"\x01\x02\xff\x00encrypted"
    assert decode_bytea(encode_bytea(payload)) == payload


def test_bytea_encoding_uses_the_postgres_hex_prefix() -> None:
    assert encode_bytea(b"\x01\x02") == "\\x0102"


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------


def test_only_syncable_statuses_are_requested() -> None:
    """Spec 7.3: everything except active and rate_limited is skipped.

    Filtered in the database, so a growing table of disabled accounts costs
    nothing to skip.
    """
    repo, transport = build_repo()
    repo.list_syncable_connections()

    status_filter = transport.last.url.params["status"]
    assert status_filter.startswith("in.(")
    assert "active" in status_filter
    assert "rate_limited" in status_filter
    assert "auth_failed" not in status_filter
    assert "disabled" not in status_filter


def test_connections_are_parsed_with_the_password_still_encrypted() -> None:
    """A connection object must be safe to log or put in an error report."""
    secret_bytes = b"\x01ciphertext"
    repo, _ = build_repo(
        [
            httpx.Response(
                200,
                json=[
                    {
                        "user_id": USER_ID,
                        "garmin_email": "runner@example.com",
                        "garmin_password_encrypted": encode_bytea(secret_bytes),
                        "status": "active",
                        "last_sync_at": "2026-01-15T06:30:00+00:00",
                    }
                ],
            )
        ]
    )

    connections = repo.list_syncable_connections()

    assert len(connections) == 1
    connection = connections[0]
    assert connection.user_id == USER_ID
    assert connection.encrypted_password == secret_bytes
    assert connection.status is ConnectionStatus.ACTIVE
    assert connection.last_sync_at == datetime(2026, 1, 15, 6, 30, tzinfo=UTC)


def test_a_null_last_sync_is_tolerated() -> None:
    """A connection that has never synced successfully."""
    repo, _ = build_repo(
        [
            httpx.Response(
                200,
                json=[
                    {
                        "user_id": USER_ID,
                        "garmin_email": "runner@example.com",
                        "garmin_password_encrypted": encode_bytea(b"x"),
                        "status": "rate_limited",
                        "last_sync_at": None,
                    }
                ],
            )
        ]
    )
    assert repo.list_syncable_connections()[0].last_sync_at is None


def test_a_failed_sync_does_not_erase_the_last_successful_one() -> None:
    """The UI shows when the last success was (spec 7.5)."""
    repo, transport = build_repo()
    repo.update_connection(USER_ID, status=ConnectionStatus.AUTH_FAILED, last_error="rejected")

    payload = body_of(transport.last)
    assert payload["status"] == "auth_failed"
    assert payload["last_error"] == "rejected"
    assert "last_sync_at" not in payload


def test_a_successful_sync_records_its_timestamp() -> None:
    repo, transport = build_repo()
    repo.update_connection(
        USER_ID,
        status=ConnectionStatus.ACTIVE,
        synced_at=datetime(2026, 1, 15, 7, 0, tzinfo=UTC),
    )

    payload = body_of(transport.last)
    assert payload["last_sync_at"].startswith("2026-01-15T07:00:00")
    assert payload["last_error"] is None, "a success must clear a stale error"


# ---------------------------------------------------------------------------
# Idempotent import (spec 5.2)
# ---------------------------------------------------------------------------


def test_inserts_ask_the_database_to_ignore_duplicates() -> None:
    """Idempotence is enforced by the unique constraint, not by bookkeeping.

    That is what makes a re-run, a concurrent run and a caller bug all safe.
    """
    repo, transport = build_repo([httpx.Response(201, json=[{"id": "a"}])])
    repo.insert_activities(USER_ID, [activity()])

    request = transport.last
    assert "resolution=ignore-duplicates" in request.headers["Prefer"]
    assert request.url.params["on_conflict"] == "user_id,garmin_activity_id"


def test_the_reported_count_is_what_was_actually_inserted() -> None:
    """Three sent, one new: a re-run must not claim to have imported three."""
    repo, _ = build_repo([httpx.Response(201, json=[{"id": "a"}])])

    inserted = repo.insert_activities(
        USER_ID,
        [activity(garmin_activity_id=i) for i in (1, 2, 3)],
    )
    assert inserted == 1


def test_reinserting_the_same_activities_reports_zero() -> None:
    repo, _ = build_repo([httpx.Response(201, json=[])])
    assert repo.insert_activities(USER_ID, [activity()]) == 0


def test_inserting_nothing_makes_no_request() -> None:
    repo, transport = build_repo()
    assert repo.insert_activities(USER_ID, []) == 0
    assert transport.requests == []


def test_the_dedup_check_asks_only_about_the_candidates() -> None:
    repo, transport = build_repo([httpx.Response(200, json=[{"garmin_activity_id": 1001}])])

    existing = repo.existing_activity_ids(USER_ID, [1001, 1002, 1003])

    assert existing == {1001}
    params = transport.last.url.params
    assert params["user_id"] == f"eq.{USER_ID}"
    assert params["garmin_activity_id"] == "in.(1001,1002,1003)"


def test_the_dedup_check_short_circuits_on_an_empty_list() -> None:
    repo, transport = build_repo()
    assert repo.existing_activity_ids(USER_ID, []) == set()
    assert transport.requests == []


# ---------------------------------------------------------------------------
# Row rendering
# ---------------------------------------------------------------------------


def test_a_row_carries_every_metric() -> None:
    repo, transport = build_repo([httpx.Response(201, json=[{"id": "a"}])])
    repo.insert_activities(
        USER_ID,
        [
            activity(
                elevation_gain_meters=126.0,
                avg_heart_rate=152,
                max_heart_rate=178,
                avg_pace_seconds_per_km=315.5,
            )
        ],
    )

    row = body_of(transport.last)[0]
    assert row["user_id"] == USER_ID
    assert row["garmin_activity_id"] == 1001
    assert row["type"] == "running"
    assert row["started_at"].startswith("2026-01-15T06:30:00")
    assert row["duration_seconds"] == 2535
    assert row["elevation_gain_meters"] == 126.0
    assert row["avg_heart_rate"] == 152


def test_a_track_is_sent_as_wkt_with_an_srid() -> None:
    """Longitude first. Reversed, every Czech activity lands in the ocean."""
    repo, transport = build_repo([httpx.Response(201, json=[{"id": "a"}])])
    repo.insert_activities(
        USER_ID,
        [activity(track=(TrackPoint(50.08, 14.42), TrackPoint(50.09, 14.43)))],
    )

    track = body_of(transport.last)[0]["track"]
    assert track.startswith("SRID=4326;LINESTRING(")
    assert "14.42 50.08" in track


@pytest.mark.parametrize(
    "track",
    [(), (TrackPoint(50.08, 14.42),)],
    ids=["no-points", "one-point"],
)
def test_an_activity_without_a_usable_track_omits_the_column(
    track: tuple[TrackPoint, ...],
) -> None:
    """The column keeps its NULL; PostGIS is never handed a degenerate line."""
    repo, transport = build_repo([httpx.Response(201, json=[{"id": "a"}])])
    repo.insert_activities(USER_ID, [activity(track=track)])

    assert "track" not in body_of(transport.last)[0]


# ---------------------------------------------------------------------------
# Storage (spec 8.2)
# ---------------------------------------------------------------------------


def test_a_stream_is_gzipped_and_stored_under_the_owning_user() -> None:
    """The RLS policy matches the owner as the first path segment."""
    repo, transport = build_repo([httpx.Response(200, json={"Key": "ok"})])
    stream = {"heartRate": [140, 142, 145]}

    path = repo.upload_stream(USER_ID, 1001, stream)

    assert path == f"{USER_ID}/1001.json.gz"
    assert f"/storage/v1/object/activity-streams/{USER_ID}/1001.json.gz" in str(transport.last.url)
    assert json.loads(gzip.decompress(transport.last.content)) == stream


def test_uploading_a_stream_again_overwrites_rather_than_failing() -> None:
    """A retried run after a partial failure must not be blocked here."""
    repo, transport = build_repo([httpx.Response(200, json={"Key": "ok"})])
    repo.upload_stream(USER_ID, 1001, {})
    assert transport.last.headers["x-upsert"] == "true"


# ---------------------------------------------------------------------------
# Sync runs (spec 7.4)
# ---------------------------------------------------------------------------


def test_a_run_is_opened_and_returns_its_id() -> None:
    repo, _ = build_repo([httpx.Response(201, json=[{"id": "run-1"}])])
    assert repo.start_sync_run() == "run-1"


def test_a_finished_run_records_per_user_errors() -> None:
    """One broken account must not hide the rest (spec 7.1)."""
    repo, transport = build_repo()
    errors = [{"user_id": USER_ID, "error": "auth_failed"}]

    repo.finish_sync_run("run-1", users_processed=3, activities_imported=7, errors=errors)

    payload = body_of(transport.last)
    assert payload["users_processed"] == 3
    assert payload["activities_imported"] == 7
    assert payload["errors"] == errors
    assert payload["finished_at"]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_an_http_error_becomes_a_database_error_naming_the_operation() -> None:
    repo, _ = build_repo([httpx.Response(500, text="internal error")])
    with pytest.raises(DatabaseError, match="listing connections.*500"):
        repo.list_syncable_connections()


def test_an_error_never_echoes_the_request_body() -> None:
    """A garmin_connections write body carries an encrypted credential."""
    repo, _ = build_repo([httpx.Response(400, text="ciphertext-leaked-here")])

    with pytest.raises(DatabaseError) as excinfo:
        repo.update_connection(USER_ID, status=ConnectionStatus.ACTIVE)

    assert "ciphertext-leaked-here" not in str(excinfo.value)


def test_a_transport_failure_becomes_a_database_error() -> None:
    def fail(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    client = httpx.Client(
        base_url="https://project.supabase.co", transport=httpx.MockTransport(fail)
    )
    repo = SyncRepository(
        url="https://project.supabase.co", service_role_key=SERVICE_KEY, client=client
    )

    with pytest.raises(DatabaseError, match="ConnectError"):
        repo.list_syncable_connections()


def test_the_repository_closes_its_client() -> None:
    repo, _ = build_repo()
    with repo:
        pass
    assert repo._client.is_closed
