"""Tests for asymmetric credential sealing (ADR 0002)."""

from __future__ import annotations

import base64

import pytest

from sunder_sync.crypto.errors import DecryptionError, InvalidEncryptionKeyError
from sunder_sync.crypto.sealing import (
    KEY_BYTES,
    VERSION_SEALED_BOX,
    generate_keypair,
    parse_private_key,
    parse_public_key,
    seal_password,
    unseal_password,
)
from sunder_sync.crypto.secret import Secret
from tests.conftest import OTHER_USER_ID, TEST_USER_ID

PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="module")
def keypair() -> tuple[str, str]:
    return generate_keypair()


def test_a_generated_keypair_is_two_base64_keys(keypair: tuple[str, str]) -> None:
    private_b64, public_b64 = keypair
    assert len(base64.b64decode(private_b64)) == KEY_BYTES
    assert len(base64.b64decode(public_b64)) == KEY_BYTES
    assert private_b64 != public_b64


def test_every_generated_keypair_is_different() -> None:
    assert generate_keypair()[0] != generate_keypair()[0]


def test_roundtrip_returns_the_original_password(keypair: tuple[str, str]) -> None:
    private_b64, public_b64 = keypair
    payload = seal_password(
        Secret(PASSWORD), user_id=TEST_USER_ID, public_key=parse_public_key(public_b64)
    )
    recovered = unseal_password(
        payload, user_id=TEST_USER_ID, private_key=parse_private_key(private_b64)
    )
    assert recovered.reveal() == PASSWORD


@pytest.mark.parametrize(
    "password",
    ["a", "x" * 512, "häšlo s diakritikou", "🔐 emoji", " spaced ", "tab\tnewline\n"],
)
def test_roundtrip_preserves_awkward_passwords(keypair: tuple[str, str], password: str) -> None:
    private_b64, public_b64 = keypair
    payload = seal_password(
        Secret(password), user_id=TEST_USER_ID, public_key=parse_public_key(public_b64)
    )
    assert (
        unseal_password(
            payload, user_id=TEST_USER_ID, private_key=parse_private_key(private_b64)
        ).reveal()
        == password
    )


def test_the_public_key_alone_cannot_decrypt(keypair: tuple[str, str]) -> None:
    """The entire point of ADR 0002: the writer cannot read what it wrote.

    A different private key — which is all an attacker holding the Edge
    Function's configuration could produce — recovers nothing.
    """
    _, public_b64 = keypair
    payload = seal_password(
        Secret(PASSWORD), user_id=TEST_USER_ID, public_key=parse_public_key(public_b64)
    )

    attacker_private, _ = generate_keypair()
    with pytest.raises(DecryptionError):
        unseal_password(
            payload, user_id=TEST_USER_ID, private_key=parse_private_key(attacker_private)
        )


def test_the_stored_payload_does_not_contain_the_plaintext(keypair: tuple[str, str]) -> None:
    _, public_b64 = keypair
    payload = seal_password(
        Secret(PASSWORD), user_id=TEST_USER_ID, public_key=parse_public_key(public_b64)
    )
    assert PASSWORD.encode() not in payload
    for word in PASSWORD.split():
        assert word.encode() not in payload


def test_the_payload_is_versioned_as_a_sealed_box(keypair: tuple[str, str]) -> None:
    """Distinguishes it from the version-1 AES-GCM format, so both coexist."""
    _, public_b64 = keypair
    payload = seal_password(
        Secret(PASSWORD), user_id=TEST_USER_ID, public_key=parse_public_key(public_b64)
    )
    assert payload[0] == VERSION_SEALED_BOX


def test_each_sealing_uses_a_fresh_ephemeral_key(keypair: tuple[str, str]) -> None:
    """Fresh per message, which is what makes a fixed nonce safe."""
    _, public_b64 = keypair
    public_key = parse_public_key(public_b64)

    payloads = [
        seal_password(Secret(PASSWORD), user_id=TEST_USER_ID, public_key=public_key)
        for _ in range(25)
    ]
    ephemeral_keys = {p[1 : 1 + KEY_BYTES] for p in payloads}

    assert len(ephemeral_keys) == len(payloads)
    assert len(set(payloads)) == len(payloads)


def test_a_payload_does_not_unseal_on_another_users_row(keypair: tuple[str, str]) -> None:
    private_b64, public_b64 = keypair
    payload = seal_password(
        Secret(PASSWORD), user_id=TEST_USER_ID, public_key=parse_public_key(public_b64)
    )
    with pytest.raises(DecryptionError):
        unseal_password(payload, user_id=OTHER_USER_ID, private_key=parse_private_key(private_b64))


@pytest.mark.parametrize("index", [0, 1, 20, 33, 40, -1])
def test_any_tampered_byte_is_detected(keypair: tuple[str, str], index: int) -> None:
    private_b64, public_b64 = keypair
    payload = bytearray(
        seal_password(
            Secret(PASSWORD), user_id=TEST_USER_ID, public_key=parse_public_key(public_b64)
        )
    )
    payload[index] ^= 0x01

    with pytest.raises(DecryptionError):
        unseal_password(
            bytes(payload), user_id=TEST_USER_ID, private_key=parse_private_key(private_b64)
        )


@pytest.mark.parametrize("payload", [b"", b"\x02", b"\x02" + b"\x00" * 20])
def test_truncated_payloads_are_rejected(keypair: tuple[str, str], payload: bytes) -> None:
    private_b64, _ = keypair
    with pytest.raises(DecryptionError):
        unseal_password(payload, user_id=TEST_USER_ID, private_key=parse_private_key(private_b64))


def test_an_aes_gcm_payload_is_rejected_by_the_sealed_box_reader(
    keypair: tuple[str, str],
) -> None:
    """The version byte keeps the two formats apart (ADR 0002)."""
    from sunder_sync.crypto import encrypt_password
    from tests.conftest import TEST_KEY

    private_b64, _ = keypair
    aes_payload = encrypt_password(Secret(PASSWORD), user_id=TEST_USER_ID, key=TEST_KEY)

    with pytest.raises(DecryptionError, match="version"):
        unseal_password(
            aes_payload, user_id=TEST_USER_ID, private_key=parse_private_key(private_b64)
        )


def test_errors_never_leak_the_plaintext(keypair: tuple[str, str]) -> None:
    private_b64, public_b64 = keypair
    payload = seal_password(
        Secret(PASSWORD), user_id=TEST_USER_ID, public_key=parse_public_key(public_b64)
    )

    with pytest.raises(DecryptionError) as excinfo:
        unseal_password(payload, user_id=OTHER_USER_ID, private_key=parse_private_key(private_b64))

    assert PASSWORD not in str(excinfo.value)
    # Chained with `from None` so no frame holding key material survives.
    assert excinfo.value.__cause__ is None


@pytest.mark.parametrize("bad", ["", "   ", "not base64!!", "c2hvcnQ="])
def test_malformed_keys_are_rejected(bad: str) -> None:
    with pytest.raises(InvalidEncryptionKeyError):
        parse_public_key(bad)
    with pytest.raises(InvalidEncryptionKeyError):
        parse_private_key(bad)


def test_key_errors_never_echo_the_value() -> None:
    bad = "dGhpcyBpcyBub3QgYSByZWFsIGtleSBidXQgaXQgaXMgYmFzZTY0"
    with pytest.raises(InvalidEncryptionKeyError) as excinfo:
        parse_private_key(bad)
    assert bad not in str(excinfo.value)


def test_empty_inputs_are_rejected(keypair: tuple[str, str]) -> None:
    _, public_b64 = keypair
    public_key = parse_public_key(public_b64)

    with pytest.raises(ValueError, match="user_id"):
        seal_password(Secret(PASSWORD), user_id="", public_key=public_key)
    with pytest.raises(ValueError, match="password"):
        seal_password(Secret(""), user_id=TEST_USER_ID, public_key=public_key)
