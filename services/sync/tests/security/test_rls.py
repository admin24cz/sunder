"""Row Level Security is verified, not assumed (spec 6.2, 11.3).

Every test here runs against a real Postgres with the real policies. Two
throwaway accounts exist for the whole module; each test asserts that one of
them cannot reach the other's data, or that nobody can reach the tables the
frontend has no business touching at all.

These tests are the gate described in spec section 15, step 6: they must pass
before any real data is stored.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from sunder_sync.crypto import Secret, decrypt_password, encrypt_password
from tests.conftest import TEST_KEY
from tests.security.conftest import Account, AsUser, seed_activity

pytestmark = pytest.mark.supabase

# Tables the anon key must not be able to read a single row from.
LOCKED_TABLES = ("garmin_connections", "sync_runs")

# Tables the client may read but must never write — they are computed by the
# sync service, so a client-side insert would mean forged training history.
READ_ONLY_TABLES = ("activities", "segment_efforts", "personal_records")

# Every table the frontend touches, for the sweeping anonymous-visitor check.
USER_TABLES = ("profiles", "activities", "segments", "segment_efforts", "personal_records")


def _rows(response: httpx.Response) -> list[Any]:
    """Return the rows a PostgREST response carries, or [] if it is an error.

    Both outcomes — a 4xx and an empty 200 — are acceptable ways for RLS to deny
    a read, and which one PostgREST picks depends on whether the denial comes
    from a revoked grant or from a policy. The tests care that no row comes
    back, not about the status code.
    """
    if response.status_code >= 400:
        return []
    payload = response.json()
    return payload if isinstance(payload, list) else [payload]


# ---------------------------------------------------------------------------
# Harness sanity
# ---------------------------------------------------------------------------
# Every denial assertion below is of the form "no rows came back". A request
# rejected by the API gateway before RLS is consulted looks identical. These two
# tests prove the clients are wired correctly, so the suite cannot pass by
# failing to reach the database at all.


def test_anonymous_client_actually_reaches_the_api(anon_client: httpx.Client) -> None:
    """A blanket 401 here would make every anon denial test vacuous."""
    response = anon_client.get("/rest/v1/")
    assert response.status_code != 401, (
        "anon client was rejected by the gateway; its headers are wrong, so the "
        "anonymous-access tests below would pass without testing anything"
    )


def test_authenticated_client_actually_reaches_the_api(
    users: tuple[Account, Account], as_user: AsUser
) -> None:
    """Reads its own profile, which the policy allows — so a hit is expected."""
    alice, _ = users
    with as_user(alice) as client:
        response = client.get("/rest/v1/profiles", params={"select": "id"})
    assert response.status_code == 200, (
        f"authenticated client could not read its own profile ({response.status_code}); "
        f"the isolation tests below would pass without testing anything"
    )
    assert response.json() == [{"id": alice.id}]


# ---------------------------------------------------------------------------
# Cross-user isolation
# ---------------------------------------------------------------------------


def test_user_sees_only_their_own_activities(
    service_client: httpx.Client, users: tuple[Account, Account], as_user: AsUser
) -> None:
    """The core promise of the whole system (spec 6.2)."""
    alice, bob = users
    alice_activity = seed_activity(service_client, alice, garmin_activity_id=1001)
    bob_activity = seed_activity(service_client, bob, garmin_activity_id=1002)

    with as_user(alice) as client:
        response = client.get("/rest/v1/activities", params={"select": "id,user_id"})
    assert response.status_code == 200

    visible = response.json()
    visible_ids = {row["id"] for row in visible}
    assert alice_activity["id"] in visible_ids
    assert bob_activity["id"] not in visible_ids
    assert all(row["user_id"] == alice.id for row in visible)


def test_asking_for_another_users_row_by_id_returns_nothing(
    service_client: httpx.Client, users: tuple[Account, Account], as_user: AsUser
) -> None:
    """Knowing the primary key must not be enough — filtering happens in the DB."""
    alice, bob = users
    bob_activity = seed_activity(service_client, bob, garmin_activity_id=1003)

    with as_user(alice) as client:
        response = client.get(
            "/rest/v1/activities",
            params={"select": "*", "id": f"eq.{bob_activity['id']}"},
        )
    assert _rows(response) == []


def test_filtering_by_another_users_id_returns_nothing(
    service_client: httpx.Client, users: tuple[Account, Account], as_user: AsUser
) -> None:
    """A hand-crafted user_id filter must not widen what the policy allows."""
    alice, bob = users
    seed_activity(service_client, bob, garmin_activity_id=1004)

    with as_user(alice) as client:
        response = client.get(
            "/rest/v1/activities",
            params={"select": "*", "user_id": f"eq.{bob.id}"},
        )
    assert _rows(response) == []


def test_user_cannot_delete_another_users_activity(
    service_client: httpx.Client, users: tuple[Account, Account], as_user: AsUser
) -> None:
    """Delete is owner-scoped too; a permitted verb is not a permitted row."""
    alice, bob = users
    bob_activity = seed_activity(service_client, bob, garmin_activity_id=1005)

    with as_user(alice) as client:
        client.delete("/rest/v1/activities", params={"id": f"eq.{bob_activity['id']}"})

    # Verified with the service role: the row must still be there.
    check = service_client.get(
        "/rest/v1/activities", params={"select": "id", "id": f"eq.{bob_activity['id']}"}
    )
    assert len(check.json()) == 1, "Bob's activity was deleted by Alice"


def test_user_sees_only_their_own_profile(users: tuple[Account, Account], as_user: AsUser) -> None:
    alice, _ = users
    with as_user(alice) as client:
        response = client.get("/rest/v1/profiles", params={"select": "id"})
    assert response.status_code == 200
    assert {row["id"] for row in response.json()} == {alice.id}


# ---------------------------------------------------------------------------
# Tables the frontend must not reach at all
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table", LOCKED_TABLES)
def test_locked_table_is_unreadable_by_an_authenticated_user(
    users: tuple[Account, Account], as_user: AsUser, table: str
) -> None:
    """Spec 6.2: the frontend gets no access to garmin_connections at all."""
    alice, _ = users
    with as_user(alice) as client:
        response = client.get(f"/rest/v1/{table}", params={"select": "*"})
    assert _rows(response) == [], f"{table} leaked rows to an authenticated user"


@pytest.mark.parametrize("table", LOCKED_TABLES)
def test_locked_table_is_unreadable_by_an_anonymous_visitor(
    anon_client: httpx.Client, table: str
) -> None:
    response = anon_client.get(f"/rest/v1/{table}", params={"select": "*"})
    assert _rows(response) == [], f"{table} leaked rows to an anonymous visitor"


def test_stored_credentials_stay_invisible_even_to_their_owner(
    service_client: httpx.Client, users: tuple[Account, Account], as_user: AsUser
) -> None:
    """A real credential row exists and the owner still cannot read it.

    Stronger than the empty-table check above: this proves the emptiness comes
    from the policy, not from the table happening to have no rows.
    """
    alice, _ = users
    payload = encrypt_password(Secret("garmin-password"), user_id=alice.id, key=TEST_KEY)
    stored = service_client.post(
        "/rest/v1/garmin_connections",
        headers={"Prefer": "return=representation"},
        json={
            "user_id": alice.id,
            "garmin_email": "alice@example.com",
            # bytea over PostgREST uses Postgres hex input format.
            "garmin_password_encrypted": "\\x" + payload.hex(),
        },
    )
    stored.raise_for_status()

    try:
        with as_user(alice) as client:
            response = client.get("/rest/v1/garmin_connections", params={"select": "*"})
        assert _rows(response) == [], "owner could read their own encrypted credential"

        # Nor can they write to it — unlinking goes through an Edge Function.
        with as_user(alice) as client:
            client.delete("/rest/v1/garmin_connections", params={"user_id": f"eq.{alice.id}"})
        check = service_client.get(
            "/rest/v1/garmin_connections",
            params={"select": "user_id", "user_id": f"eq.{alice.id}"},
        )
        assert len(check.json()) == 1, "credential row was deleted through the anon key"
    finally:
        service_client.delete("/rest/v1/garmin_connections", params={"user_id": f"eq.{alice.id}"})


# ---------------------------------------------------------------------------
# Write restrictions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table", READ_ONLY_TABLES)
def test_client_cannot_insert_into_service_owned_tables(
    users: tuple[Account, Account], as_user: AsUser, table: str
) -> None:
    """No insert policy exists, so even a well-formed own-row insert must fail."""
    alice, _ = users
    payloads: dict[str, dict[str, Any]] = {
        "activities": {
            "user_id": alice.id,
            "garmin_activity_id": 999001,
            "type": "running",
            "started_at": "2026-02-01T06:00:00Z",
        },
        "segment_efforts": {
            "user_id": alice.id,
            "segment_id": "00000000-0000-4000-8000-000000000001",
            "activity_id": "00000000-0000-4000-8000-000000000002",
            "elapsed_seconds": 100,
        },
        "personal_records": {"user_id": alice.id, "category": "5k", "value": 1200},
    }

    with as_user(alice) as client:
        response = client.post(f"/rest/v1/{table}", json=payloads[table])
    assert response.status_code >= 400, f"client was able to insert into {table}"


def test_user_can_create_their_own_segment(users: tuple[Account, Account], as_user: AsUser) -> None:
    """The permissive half of the model: segments are authored in the UI."""
    alice, _ = users
    with as_user(alice) as client:
        response = client.post(
            "/rest/v1/segments",
            headers={"Prefer": "return=representation"},
            json={
                "user_id": alice.id,
                "name": "Test segment",
                "geometry": "LINESTRING(14.42 50.08, 14.43 50.09)",
            },
        )
        assert response.status_code < 400, response.text
        created = response.json()[0]
        client.delete("/rest/v1/segments", params={"id": f"eq.{created['id']}"})


def test_user_cannot_create_a_segment_owned_by_someone_else(
    users: tuple[Account, Account], as_user: AsUser
) -> None:
    """This is what WITH CHECK on the insert policy is for."""
    alice, bob = users
    with as_user(alice) as client:
        response = client.post(
            "/rest/v1/segments",
            json={
                "user_id": bob.id,
                "name": "Planted segment",
                "geometry": "LINESTRING(14.42 50.08, 14.43 50.09)",
            },
        )
    assert response.status_code >= 400, "Alice created a segment owned by Bob"


@pytest.mark.parametrize("table", USER_TABLES)
def test_anonymous_visitor_reads_nothing_anywhere(anon_client: httpx.Client, table: str) -> None:
    """RLS policies target `authenticated`; anon is never named."""
    response = anon_client.get(f"/rest/v1/{table}", params={"select": "*"})
    assert _rows(response) == [], f"{table} leaked rows to an anonymous visitor"


# ---------------------------------------------------------------------------
# Encryption at rest, end to end (spec 11.3, item 3)
# ---------------------------------------------------------------------------


def test_credential_is_stored_encrypted_and_decrypts_back(
    service_client: httpx.Client, users: tuple[Account, Account]
) -> None:
    """What lands in the column is ciphertext, and the key recovers it."""
    _, bob = users
    password = "s3cr3t-garmin-password"
    payload = encrypt_password(Secret(password), user_id=bob.id, key=TEST_KEY)

    service_client.post(
        "/rest/v1/garmin_connections",
        json={
            "user_id": bob.id,
            "garmin_email": "bob@example.com",
            "garmin_password_encrypted": "\\x" + payload.hex(),
        },
    ).raise_for_status()

    try:
        response = service_client.get(
            "/rest/v1/garmin_connections",
            params={"select": "garmin_password_encrypted", "user_id": f"eq.{bob.id}"},
        )
        response.raise_for_status()
        stored_hex: str = response.json()[0]["garmin_password_encrypted"]
        stored = bytes.fromhex(stored_hex.removeprefix("\\x"))

        assert stored == payload
        assert password.encode() not in stored
        assert decrypt_password(stored, user_id=bob.id, key=TEST_KEY).reveal() == password
    finally:
        service_client.delete("/rest/v1/garmin_connections", params={"user_id": f"eq.{bob.id}"})
