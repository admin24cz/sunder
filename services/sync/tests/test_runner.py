"""Tests for the sync run (spec 7.1, 7.3, 7.4).

The point of this module is isolation: one user's failure must not touch
another's. Most tests here deliberately construct a broken user alongside a
working one and assert that the working one still finishes.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import pytest

from sunder_sync.config import SyncConfig
from sunder_sync.crypto import Secret, encrypt_password
from sunder_sync.domain import ConnectionStatus
from sunder_sync.garmin import (
    GarminAuthError,
    GarminClient,
    GarminRateLimitedError,
    RateLimiter,
    RetryPolicy,
)
from sunder_sync.models import GarminConnection, ParsedActivity
from sunder_sync.runner import run_sync, sync_user
from tests.conftest import TEST_KEY
from tests.test_garmin_client import FakeGarminApi
from tests.test_throttle import FakeClock

ALICE = "11111111-1111-4111-8111-111111111111"
BOB = "22222222-2222-4222-8222-222222222222"


def connection(user_id: str, password: str = "garmin-password") -> GarminConnection:  # noqa: S107
    return GarminConnection(
        user_id=user_id,
        garmin_email=f"{user_id[:4]}@example.com",
        encrypted_password=encrypt_password(Secret(password), user_id=user_id, key=TEST_KEY),
        status=ConnectionStatus.ACTIVE,
    )


def summary(activity_id: int) -> dict[str, Any]:
    return {
        "activityId": activity_id,
        "activityType": {"typeKey": "running"},
        "startTimeGMT": "2026-01-15 06:30:00",
        "duration": 2535.0,
        "distance": 10520.0,
    }


class FakeRepository:
    """In-memory stand-in for `SyncRepository`."""

    def __init__(
        self,
        connections: list[GarminConnection] | None = None,
        *,
        existing: set[int] | None = None,
    ) -> None:
        self.connections = connections or []
        self.existing = existing or set()
        self.inserted: dict[str, list[ParsedActivity]] = {}
        self.status_updates: list[dict[str, Any]] = []
        self.runs: list[dict[str, Any]] = []
        self.finished: list[dict[str, Any]] = []

    def list_syncable_connections(self) -> list[GarminConnection]:
        return self.connections

    def existing_activity_ids(self, user_id: str, candidate_ids: list[int]) -> set[int]:
        del user_id
        return {i for i in candidate_ids if i in self.existing}

    def insert_activities(self, user_id: str, activities: list[ParsedActivity]) -> int:
        self.inserted.setdefault(user_id, []).extend(activities)
        return len(activities)

    def update_connection(
        self,
        user_id: str,
        *,
        status: ConnectionStatus,
        last_error: str | None = None,
        synced_at: object = None,
    ) -> None:
        self.status_updates.append(
            {
                "user_id": user_id,
                "status": status,
                "last_error": last_error,
                "synced_at": synced_at,
            }
        )

    def start_sync_run(self) -> str:
        self.runs.append({})
        return f"run-{len(self.runs)}"

    def finish_sync_run(
        self,
        run_id: str,
        *,
        users_processed: int,
        activities_imported: int,
        errors: list[dict[str, str]],
    ) -> None:
        self.finished.append(
            {
                "run_id": run_id,
                "users_processed": users_processed,
                "activities_imported": activities_imported,
                "errors": errors,
            }
        )

    def status_for(self, user_id: str) -> ConnectionStatus | None:
        for update in reversed(self.status_updates):
            if update["user_id"] == user_id:
                status: ConnectionStatus = update["status"]
                return status
        return None


def make_config(**overrides: Any) -> SyncConfig:
    defaults: dict[str, Any] = {
        "supabase_url": "https://project.supabase.co",
        "service_role_key": "sb_secret_test",
        "encryption_key": TEST_KEY,
        "max_activities_per_user": 50,
    }
    defaults.update(overrides)
    return SyncConfig(**defaults)


def client_factory_for(*apis: FakeGarminApi) -> Callable[[], GarminClient]:
    """Return a factory handing out each fake in turn, one per user."""
    remaining = list(apis)

    def factory() -> GarminClient:
        api = remaining.pop(0)
        clock = FakeClock()
        return GarminClient(
            api_factory=lambda _email, _password: api,
            rate_limiter=RateLimiter(sleep=clock.sleep, monotonic=clock.monotonic),
            retry_policy=RetryPolicy(sleep=clock.sleep, jitter=lambda: 0.0),
        )

    return factory


def api_with(activity_ids: Sequence[int], **kwargs: Any) -> FakeGarminApi:
    return FakeGarminApi(activities=[summary(i) for i in activity_ids], **kwargs)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_new_activities_are_imported() -> None:
    repo = FakeRepository([connection(ALICE)])
    summary_ = run_sync(
        repository=repo,  # type: ignore[arg-type]
        config=make_config(),
        client_factory=client_factory_for(api_with([1001, 1002])),
    )

    assert summary_.activities_imported == 2
    assert {a.garmin_activity_id for a in repo.inserted[ALICE]} == {1001, 1002}


def test_already_stored_activities_are_not_reimported() -> None:
    """Spec 5.2: a second run over the same data imports nothing."""
    repo = FakeRepository([connection(ALICE)], existing={1001, 1002})
    summary_ = run_sync(
        repository=repo,  # type: ignore[arg-type]
        config=make_config(),
        client_factory=client_factory_for(api_with([1001, 1002])),
    )

    assert summary_.activities_imported == 0
    assert ALICE not in repo.inserted


def test_only_the_new_activities_are_imported() -> None:
    repo = FakeRepository([connection(ALICE)], existing={1001})
    run_sync(
        repository=repo,  # type: ignore[arg-type]
        config=make_config(),
        client_factory=client_factory_for(api_with([1001, 1002, 1003])),
    )

    assert {a.garmin_activity_id for a in repo.inserted[ALICE]} == {1002, 1003}


def test_a_successful_sync_records_active_and_a_timestamp() -> None:
    repo = FakeRepository([connection(ALICE)])
    run_sync(
        repository=repo,  # type: ignore[arg-type]
        config=make_config(),
        client_factory=client_factory_for(api_with([1001])),
    )

    update = repo.status_updates[-1]
    assert update["status"] is ConnectionStatus.ACTIVE
    assert update["last_error"] is None
    assert update["synced_at"] is not None


def test_a_user_with_no_activities_is_not_an_error() -> None:
    repo = FakeRepository([connection(ALICE)])
    summary_ = run_sync(
        repository=repo,  # type: ignore[arg-type]
        config=make_config(),
        client_factory=client_factory_for(api_with([])),
    )

    assert summary_.failed == 0
    assert repo.status_for(ALICE) is ConnectionStatus.ACTIVE


# ---------------------------------------------------------------------------
# Per-user isolation (spec 7.1) — the reason this module exists
# ---------------------------------------------------------------------------


def test_one_users_auth_failure_does_not_stop_another_user() -> None:
    repo = FakeRepository([connection(ALICE), connection(BOB)])
    summary_ = run_sync(
        repository=repo,  # type: ignore[arg-type]
        config=make_config(),
        client_factory=client_factory_for(
            FakeGarminApi(login_error=GarminAuthError("rejected")),
            api_with([2001, 2002]),
        ),
    )

    assert summary_.users_processed == 2
    assert summary_.failed == 1
    # Bob's activities still landed.
    assert summary_.activities_imported == 2
    assert {a.garmin_activity_id for a in repo.inserted[BOB]} == {2001, 2002}


def test_a_failing_user_gets_the_status_their_error_implies() -> None:
    """Spec 7.3: the error type determines the state, not a generic failure."""
    repo = FakeRepository([connection(ALICE), connection(BOB)])
    run_sync(
        repository=repo,  # type: ignore[arg-type]
        config=make_config(),
        client_factory=client_factory_for(
            FakeGarminApi(login_error=GarminAuthError("rejected")),
            FakeGarminApi(login_error=GarminRateLimitedError("429")),
        ),
    )

    assert repo.status_for(ALICE) is ConnectionStatus.AUTH_FAILED
    assert repo.status_for(BOB) is ConnectionStatus.RATE_LIMITED


def test_an_unexpected_error_leaves_the_connection_active() -> None:
    """Our bug must not tell the user to re-link a working account."""
    repo = FakeRepository([connection(ALICE)])
    summary_ = run_sync(
        repository=repo,  # type: ignore[arg-type]
        config=make_config(),
        client_factory=client_factory_for(
            FakeGarminApi(login_error=ValueError("something in our code broke"))
        ),
    )

    assert summary_.failed == 1
    assert repo.status_for(ALICE) is ConnectionStatus.ACTIVE


def test_a_credential_that_cannot_be_decrypted_fails_only_that_user() -> None:
    """A key rotated without re-encrypting, or a relocated ciphertext."""
    corrupted = GarminConnection(
        user_id=ALICE,
        garmin_email="alice@example.com",
        encrypted_password=b"\x01not a real payload",
        status=ConnectionStatus.ACTIVE,
    )
    repo = FakeRepository([corrupted, connection(BOB)])

    summary_ = run_sync(
        repository=repo,  # type: ignore[arg-type]
        config=make_config(),
        client_factory=client_factory_for(api_with([2001])),
    )

    assert summary_.failed == 1
    assert summary_.activities_imported == 1


def test_a_failure_to_record_status_does_not_abandon_the_remaining_users() -> None:
    """Losing one status update beats losing everyone else's import."""
    repo = FakeRepository([connection(ALICE), connection(BOB)])

    original = repo.update_connection
    calls = {"n": 0}

    def flaky(*args: Any, **kwargs: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("bookkeeping write failed")
        original(*args, **kwargs)

    repo.update_connection = flaky  # type: ignore[method-assign]

    summary_ = run_sync(
        repository=repo,  # type: ignore[arg-type]
        config=make_config(),
        client_factory=client_factory_for(api_with([1001]), api_with([2001])),
    )

    assert summary_.users_processed == 2
    assert summary_.activities_imported == 2


def test_an_unparseable_activity_does_not_cost_the_rest_of_the_page() -> None:
    """Isolation applied one level down from users."""
    api = FakeGarminApi(
        activities=[summary(1001), {"garbage": True}, summary(1003)],
    )
    repo = FakeRepository([connection(ALICE)])

    summary_ = run_sync(
        repository=repo,  # type: ignore[arg-type]
        config=make_config(),
        client_factory=client_factory_for(api),
    )

    assert summary_.activities_imported == 2


# ---------------------------------------------------------------------------
# Sync run bookkeeping (spec 7.4)
# ---------------------------------------------------------------------------


def test_a_run_is_opened_and_closed() -> None:
    repo = FakeRepository([connection(ALICE)])
    run_sync(
        repository=repo,  # type: ignore[arg-type]
        config=make_config(),
        client_factory=client_factory_for(api_with([1001])),
    )

    assert len(repo.runs) == 1
    assert repo.finished[0]["run_id"] == "run-1"
    assert repo.finished[0]["users_processed"] == 1
    assert repo.finished[0]["activities_imported"] == 1


def test_the_run_records_one_entry_per_failed_user() -> None:
    repo = FakeRepository([connection(ALICE), connection(BOB)])
    run_sync(
        repository=repo,  # type: ignore[arg-type]
        config=make_config(),
        client_factory=client_factory_for(
            FakeGarminApi(login_error=GarminAuthError("rejected")),
            api_with([2001]),
        ),
    )

    errors = repo.finished[0]["errors"]
    assert len(errors) == 1
    assert errors[0]["user_id"] == ALICE
    assert errors[0]["status"] == "auth_failed"


def test_the_run_is_closed_even_when_listing_connections_fails() -> None:
    """A run must never vanish from the log it exists to provide."""
    repo = FakeRepository()

    def explode() -> list[GarminConnection]:
        raise RuntimeError("database unreachable")

    repo.list_syncable_connections = explode  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="database unreachable"):
        run_sync(
            repository=repo,  # type: ignore[arg-type]
            config=make_config(),
            client_factory=client_factory_for(),
        )

    assert len(repo.finished) == 1


def test_errors_never_contain_the_password() -> None:
    """Spec 6.4, on the path where a failure message is persisted."""
    password = "very-secret-password"
    repo = FakeRepository([connection(ALICE, password=password)])

    run_sync(
        repository=repo,  # type: ignore[arg-type]
        config=make_config(),
        client_factory=client_factory_for(FakeGarminApi(login_error=GarminAuthError("rejected"))),
    )

    serialised = str(repo.finished[0]["errors"]) + str(repo.status_updates)
    assert password not in serialised


# ---------------------------------------------------------------------------
# Request budget (spec 7.2)
# ---------------------------------------------------------------------------


def test_the_page_size_limits_what_one_run_asks_for() -> None:
    """Backfill is spread across runs rather than done as one large import."""
    api = api_with([1001])
    repo = FakeRepository([connection(ALICE)])

    sync_user(
        connection(ALICE),
        repository=repo,  # type: ignore[arg-type]
        config=make_config(max_activities_per_user=10),
        client_factory=client_factory_for(api),
    )

    assert api.list_calls == [(0, 10)]
