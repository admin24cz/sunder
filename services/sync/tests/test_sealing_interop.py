"""The Edge Function and the sync service must agree on the wire format.

Credential sealing is implemented twice: in TypeScript in
`supabase/functions/link-garmin/index.ts`, which writes, and in Python in
`sunder_sync.crypto.sealing`, which reads. Nothing in either language's type
system connects them.

That makes drift between them the most dangerous kind of bug this project can
have. It would not fail loudly at link time — the Edge Function would happily
seal a password and report success — and it would surface hours later as every
sync failing to decrypt, with the user's real password already gone from the
browser. Recovery would mean asking every user to re-link.

So the format is pinned by a golden vector: a payload produced by the TypeScript
implementation, checked into the repository, that Python must be able to open.
Regenerating it requires deliberately running the JavaScript side, which is
exactly the friction a format change should have.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sunder_sync.crypto.errors import DecryptionError
from sunder_sync.crypto.opening import CredentialKeys, open_credential
from sunder_sync.crypto.sealing import (
    VERSION_SEALED_BOX,
    generate_keypair,
    parse_private_key,
    unseal_password,
)
from tests.conftest import OTHER_USER_ID, TEST_KEY

FIXTURE = Path(__file__).parent / "fixtures" / "sealed_credential_from_edge_function.json"


@pytest.fixture(scope="module")
def vector() -> dict[str, Any]:
    """The payload the TypeScript implementation produced."""
    data: dict[str, Any] = json.loads(FIXTURE.read_text())
    return data


def test_python_can_open_what_the_edge_function_sealed(vector: dict[str, Any]) -> None:
    """The whole point. If this fails, linking an account is silently broken."""
    payload = bytes.fromhex(vector["sealed_payload_hex"])
    recovered = unseal_password(
        payload,
        user_id=vector["user_id"],
        private_key=parse_private_key(vector["private_key_base64"]),
    )
    assert recovered.reveal() == vector["password"]


def test_the_dispatcher_also_handles_an_edge_function_payload(
    vector: dict[str, Any],
) -> None:
    """`open_credential` is what the runner calls, so it must route this too."""
    keys = CredentialKeys(
        encryption_key=TEST_KEY,
        credential_private_key=parse_private_key(vector["private_key_base64"]),
    )
    recovered = open_credential(
        bytes.fromhex(vector["sealed_payload_hex"]), user_id=vector["user_id"], keys=keys
    )
    assert recovered.reveal() == vector["password"]


def test_the_vector_uses_the_documented_layout(vector: dict[str, Any]) -> None:
    """version(1) || ephemeral public key(32) || ciphertext+tag(len+16)."""
    payload = bytes.fromhex(vector["sealed_payload_hex"])

    assert payload[0] == VERSION_SEALED_BOX
    expected_length = 1 + 32 + len(vector["password"].encode()) + 16
    assert len(payload) == expected_length


def test_the_vector_does_not_contain_its_own_plaintext(vector: dict[str, Any]) -> None:
    payload = bytes.fromhex(vector["sealed_payload_hex"])
    assert vector["password"].encode() not in payload


def test_the_vector_is_bound_to_its_user(vector: dict[str, Any]) -> None:
    """Cross-language too: the AAD must be constructed identically on both sides."""
    with pytest.raises(DecryptionError):
        unseal_password(
            bytes.fromhex(vector["sealed_payload_hex"]),
            user_id=OTHER_USER_ID,
            private_key=parse_private_key(vector["private_key_base64"]),
        )


def test_another_private_key_cannot_open_the_vector(vector: dict[str, Any]) -> None:
    """Confirms the vector is genuinely sealed, not merely encoded."""
    other_private, _ = generate_keypair()
    with pytest.raises(DecryptionError):
        unseal_password(
            bytes.fromhex(vector["sealed_payload_hex"]),
            user_id=vector["user_id"],
            private_key=parse_private_key(other_private),
        )
