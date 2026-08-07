"""Fixtures for security tests that need a live Supabase instance.

These tests talk to a real Postgres with real RLS, because that is the only
thing that actually proves the policies work — a mock would only prove that the
mock agrees with our assumptions.

Point them at either a local stack (`supabase start`) or a throwaway project:

    export SUPABASE_URL=http://127.0.0.1:54321
    export SUPABASE_ANON_KEY=...
    export SUPABASE_SERVICE_ROLE_KEY=...
    pytest tests/security

Without those variables the whole module skips, so the rest of the suite still
runs on a laptop with no stack running.

Warning:
    Never point these at the production project. The fixtures create and delete
    users.
"""

from __future__ import annotations

import os
import secrets
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

REQUEST_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class SupabaseEnv:
    """Connection details for the instance under test."""

    url: str
    anon_key: str
    service_role_key: str


@dataclass(frozen=True)
class Account:
    """A throwaway account under test, plus a signed-in access token."""

    id: str
    email: str
    access_token: str


@pytest.fixture(scope="session")
def supabase_env() -> SupabaseEnv:
    """Read connection details, or skip the whole module."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    anon_key = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()

    missing = [
        name
        for name, value in (
            ("SUPABASE_URL", url),
            ("SUPABASE_ANON_KEY", anon_key),
            ("SUPABASE_SERVICE_ROLE_KEY", service_role_key),
        )
        if not value
    ]
    if missing:
        pytest.skip(f"live Supabase not configured; missing {', '.join(missing)}")

    return SupabaseEnv(url=url, anon_key=anon_key, service_role_key=service_role_key)


@pytest.fixture(scope="session")
def service_client(supabase_env: SupabaseEnv) -> Iterator[httpx.Client]:
    """HTTP client authenticated as the service role.

    The service role bypasses RLS, so this is used only to set data up and tear
    it down — never to make an assertion about what a user may see.
    """
    with httpx.Client(
        base_url=supabase_env.url,
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={
            "apikey": supabase_env.service_role_key,
            "Authorization": f"Bearer {supabase_env.service_role_key}",
            "Content-Type": "application/json",
        },
    ) as client:
        yield client


def _create_user(client: httpx.Client, supabase_env: SupabaseEnv) -> Account:
    """Create a confirmed throwaway user and sign it in."""
    email = f"rls-test-{uuid.uuid4().hex[:12]}@sunder.test"
    password = secrets.token_urlsafe(24)

    created = client.post(
        "/auth/v1/admin/users",
        json={"email": email, "password": password, "email_confirm": True},
    )
    created.raise_for_status()
    user_id: str = created.json()["id"]

    # Sign in through the public endpoint with the anon key — the same path the
    # frontend takes, so the resulting token carries the same claims.
    signed_in = client.post(
        "/auth/v1/token",
        params={"grant_type": "password"},
        json={"email": email, "password": password},
        headers={"apikey": supabase_env.anon_key, "Authorization": ""},
    )
    signed_in.raise_for_status()
    return Account(id=user_id, email=email, access_token=signed_in.json()["access_token"])


@pytest.fixture(scope="session")
def users(
    service_client: httpx.Client, supabase_env: SupabaseEnv
) -> Iterator[tuple[Account, Account]]:
    """Two unrelated accounts — the minimum needed to test isolation."""
    alice = _create_user(service_client, supabase_env)
    bob = _create_user(service_client, supabase_env)
    try:
        yield alice, bob
    finally:
        # Deleting the auth user cascades to every table via ON DELETE CASCADE,
        # so this is the whole cleanup.
        for user in (alice, bob):
            service_client.delete(f"/auth/v1/admin/users/{user.id}")


type AsUser = Callable[[Account], httpx.Client]
"""Factory returned by the `as_user` fixture."""


@pytest.fixture
def as_user(supabase_env: SupabaseEnv) -> AsUser:
    """Return a factory for clients scoped to one user, as the frontend is.

    The anon key goes in `apikey` and the user's JWT in `Authorization`, exactly
    as supabase-js sends them — so `auth.uid()` inside the policies resolves the
    same way it will in production.
    """

    def _factory(user: Account) -> httpx.Client:
        return httpx.Client(
            base_url=supabase_env.url,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={
                "apikey": supabase_env.anon_key,
                "Authorization": f"Bearer {user.access_token}",
                "Content-Type": "application/json",
            },
        )

    return _factory


@pytest.fixture
def anon_client(supabase_env: SupabaseEnv) -> Iterator[httpx.Client]:
    """Client with the anon key and no session — a logged-out visitor."""
    with httpx.Client(
        base_url=supabase_env.url,
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={
            "apikey": supabase_env.anon_key,
            "Content-Type": "application/json",
        },
    ) as client:
        yield client


def seed_activity(
    service_client: httpx.Client,
    user: Account,
    *,
    garmin_activity_id: int,
) -> dict[str, Any]:
    """Insert one activity for `user` using the service role.

    Returns:
        The inserted row, so tests can assert on its id.
    """
    response = service_client.post(
        "/rest/v1/activities",
        headers={"Prefer": "return=representation"},
        json={
            "user_id": user.id,
            "garmin_activity_id": garmin_activity_id,
            "type": "running",
            "started_at": "2026-01-15T06:30:00Z",
            "duration_seconds": 1800,
            "distance_meters": 5000,
        },
    )
    response.raise_for_status()
    row: dict[str, Any] = response.json()[0]
    return row
