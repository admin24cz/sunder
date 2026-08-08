"""Tests for interactive Garmin authorisation (ADR 0003).

Garmin is never contacted: `garminconnect.Garmin` is replaced in the module's
namespace at import time. What these tests actually guard is the promise the
whole change is for — that the password is used once and never stored.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from sunder_sync.authorize import AuthorizationError, authorize
from sunder_sync.crypto import generate_keypair, parse_private_key, unseal_password
from sunder_sync.crypto.sealing import VERSION_SEALED_BOX

USER_ID = "11111111-1111-4111-8111-111111111111"
EMAIL = "runner@example.com"
PASSWORD = "very-secret-garmin-password"
MFA_CODE = "123456"
TOKENS = "eyJvYXV0aDEiOiJ0b2tlbiJ9" * 30  # long, like a real garth dump


class FakeGarth:
    def __init__(self, tokens: str) -> None:
        self._tokens = tokens

    def dumps(self) -> str:
        return self._tokens


class FakeGarmin:
    """Stand-in for `garminconnect.Garmin`, recording what it was given."""

    instances: list[FakeGarmin] = []

    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        *,
        prompt_mfa: Any = None,
        **_kwargs: Any,
    ) -> None:
        self.email = email
        self.password = password
        self.prompt_mfa = prompt_mfa
        self.login_calls = 0
        self.mfa_requested = False
        self.garth = FakeGarth(TOKENS)
        self.login_error: Exception | None = None
        FakeGarmin.instances.append(self)

    def login(self, tokenstore: str | None = None) -> object:
        del tokenstore
        self.login_calls += 1
        if self.login_error is not None:
            raise self.login_error
        # Mirror the real flow: Garmin asks for a code when MFA is enabled.
        if self.prompt_mfa is not None and _MFA_ENABLED:
            self.mfa_requested = True
            self.prompt_mfa()
        return object()


_MFA_ENABLED = False


class FakeRepository:
    def __init__(self) -> None:
        self.stored: list[tuple[str, bytes]] = []

    def store_tokens(self, user_id: str, sealed_tokens: bytes) -> None:
        self.stored.append((user_id, sealed_tokens))


@pytest.fixture(autouse=True)
def fake_garminconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a fake `garminconnect` module for the local import to find."""
    FakeGarmin.instances.clear()
    module = types.ModuleType("garminconnect")
    module.Garmin = FakeGarmin  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "garminconnect", module)


@pytest.fixture(scope="module")
def keypair() -> tuple[str, str]:
    return generate_keypair()


def run(
    keypair: tuple[str, str],
    repo: FakeRepository,
    *,
    answers: list[str] | None = None,
    password: str = PASSWORD,
) -> str:
    """Call `authorize` with scripted prompts."""
    pending = list(answers or [MFA_CODE])
    return authorize(
        USER_ID,
        repository=repo,  # type: ignore[arg-type]
        credential_public_key_base64=keypair[1],
        email=EMAIL,
        prompt=lambda _label: pending.pop(0),
        secret_prompt=lambda _label: password,
    )


def test_the_stored_value_is_the_session_not_the_password(
    keypair: tuple[str, str],
) -> None:
    """The whole point of ADR 0003."""
    repo = FakeRepository()
    run(keypair, repo)

    assert len(repo.stored) == 1
    user_id, sealed = repo.stored[0]
    assert user_id == USER_ID

    recovered = unseal_password(sealed, user_id=USER_ID, private_key=parse_private_key(keypair[0]))
    assert recovered.reveal() == TOKENS
    assert recovered.reveal() != PASSWORD


def test_the_password_never_reaches_the_stored_payload(keypair: tuple[str, str]) -> None:
    repo = FakeRepository()
    run(keypair, repo)
    _, sealed = repo.stored[0]
    assert PASSWORD.encode() not in sealed


def test_the_session_is_sealed_not_merely_encoded(keypair: tuple[str, str]) -> None:
    repo = FakeRepository()
    run(keypair, repo)
    _, sealed = repo.stored[0]

    assert sealed[0] == VERSION_SEALED_BOX
    assert TOKENS.encode() not in sealed

    # Bound to this user, like every other credential (ADR 0002).
    from sunder_sync.crypto.errors import DecryptionError

    with pytest.raises(DecryptionError):
        unseal_password(
            sealed,
            user_id="22222222-2222-4222-8222-222222222222",
            private_key=parse_private_key(keypair[0]),
        )


def test_an_mfa_code_is_supplied_when_garmin_asks(
    keypair: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reason a human is needed at all."""
    monkeypatch.setattr("tests.test_authorize._MFA_ENABLED", True)
    repo = FakeRepository()
    run(keypair, repo, answers=[MFA_CODE])

    assert FakeGarmin.instances[0].mfa_requested
    assert repo.stored, "authorisation should still complete after the MFA prompt"


def test_the_password_is_passed_to_garmin_and_nowhere_else(
    keypair: tuple[str, str],
) -> None:
    repo = FakeRepository()
    run(keypair, repo)

    api = FakeGarmin.instances[0]
    assert api.password == PASSWORD
    assert api.email == EMAIL
    assert api.login_calls == 1


def test_an_empty_password_is_refused_before_contacting_garmin(
    keypair: tuple[str, str],
) -> None:
    repo = FakeRepository()
    with pytest.raises(AuthorizationError, match="password"):
        run(keypair, repo, password="")
    assert FakeGarmin.instances == []
    assert repo.stored == []


def test_nothing_is_stored_when_the_login_fails(keypair: tuple[str, str]) -> None:
    """A failed authorisation must not leave a half-written connection."""
    repo = FakeRepository()

    class FailingGarmin(FakeGarmin):
        def login(self, tokenstore: str | None = None) -> object:  # noqa: ARG002
            raise RuntimeError("Garmin said no")

    module = sys.modules["garminconnect"]
    module.Garmin = FailingGarmin  # type: ignore[attr-defined]

    with pytest.raises(AuthorizationError, match="Garmin login failed"):
        run(keypair, repo)
    assert repo.stored == []


def test_a_failure_message_never_contains_the_password(
    keypair: tuple[str, str],
) -> None:
    repo = FakeRepository()

    class FailingGarmin(FakeGarmin):
        def login(self, tokenstore: str | None = None) -> object:  # noqa: ARG002
            raise RuntimeError(f"rejected password {PASSWORD}")

    module = sys.modules["garminconnect"]
    module.Garmin = FailingGarmin  # type: ignore[attr-defined]

    with pytest.raises(AuthorizationError) as excinfo:
        run(keypair, repo)
    # The classifier reports the class of failure, not the library's text.
    assert PASSWORD not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
