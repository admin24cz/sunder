"""Tests for AES-256-GCM encryption of Garmin passwords (spec 6.1, 11.3)."""

from __future__ import annotations

import pytest

from sunder_sync.crypto import (
    NONCE_BYTES,
    VERSION_AES_256_GCM,
    DecryptionError,
    Secret,
    decrypt_password,
    encrypt_password,
)
from tests.conftest import OTHER_USER_ID, TEST_USER_ID

PASSWORD = "correct horse battery staple"


def test_roundtrip_returns_the_original_password(key: bytes) -> None:
    payload = encrypt_password(Secret(PASSWORD), user_id=TEST_USER_ID, key=key)
    assert decrypt_password(payload, user_id=TEST_USER_ID, key=key).reveal() == PASSWORD


@pytest.mark.parametrize(
    "password",
    [
        pytest.param("a", id="single-character"),
        pytest.param("x" * 512, id="very-long"),
        pytest.param("häšlo s diakritikou", id="utf8-multibyte"),
        pytest.param("🔐 emoji password", id="outside-bmp"),
        pytest.param(" leading and trailing ", id="surrounding-whitespace"),
        pytest.param("tab\tand\nnewline", id="control-characters"),
    ],
)
def test_roundtrip_preserves_awkward_passwords(key: bytes, password: str) -> None:
    payload = encrypt_password(Secret(password), user_id=TEST_USER_ID, key=key)
    assert decrypt_password(payload, user_id=TEST_USER_ID, key=key).reveal() == password


def test_stored_payload_does_not_contain_the_plaintext(key: bytes) -> None:
    """Spec 11.3: the value written to the database must not be readable."""
    payload = encrypt_password(Secret(PASSWORD), user_id=TEST_USER_ID, key=key)
    assert PASSWORD.encode() not in payload
    for word in PASSWORD.split():
        assert word.encode() not in payload


def test_payload_layout_is_version_nonce_ciphertext(key: bytes) -> None:
    payload = encrypt_password(Secret(PASSWORD), user_id=TEST_USER_ID, key=key)
    assert payload[0] == VERSION_AES_256_GCM
    # version byte + nonce + ciphertext (== plaintext length) + 16-byte GCM tag
    assert len(payload) == 1 + NONCE_BYTES + len(PASSWORD.encode()) + 16


def test_each_encryption_uses_a_fresh_nonce(key: bytes) -> None:
    """Nonce reuse under one key is the catastrophic failure mode of GCM."""
    payloads = [
        encrypt_password(Secret(PASSWORD), user_id=TEST_USER_ID, key=key) for _ in range(50)
    ]
    nonces = {p[1 : 1 + NONCE_BYTES] for p in payloads}
    assert len(nonces) == len(payloads)
    # Identical plaintext must therefore never produce identical ciphertext.
    assert len(set(payloads)) == len(payloads)


def test_wrong_key_fails_to_decrypt(key: bytes) -> None:
    payload = encrypt_password(Secret(PASSWORD), user_id=TEST_USER_ID, key=key)
    other_key = bytes(b ^ 0xFF for b in key)
    with pytest.raises(DecryptionError):
        decrypt_password(payload, user_id=TEST_USER_ID, key=other_key)


def test_payload_does_not_decrypt_on_another_users_row(key: bytes) -> None:
    """The user id is authenticated, so a ciphertext cannot be relocated.

    Without this, someone with write access to the database could copy Alice's
    encrypted password onto Bob's row and have the sync service log into Alice's
    Garmin account during Bob's run.
    """
    payload = encrypt_password(Secret(PASSWORD), user_id=TEST_USER_ID, key=key)
    with pytest.raises(DecryptionError):
        decrypt_password(payload, user_id=OTHER_USER_ID, key=key)


@pytest.mark.parametrize("index", [0, 1, 5, 13, 20, -1])
def test_any_tampered_byte_is_detected(key: bytes, index: int) -> None:
    """Authenticated encryption: a modified row must fail, not decrypt wrongly."""
    payload = bytearray(encrypt_password(Secret(PASSWORD), user_id=TEST_USER_ID, key=key))
    payload[index] ^= 0x01
    with pytest.raises(DecryptionError):
        decrypt_password(bytes(payload), user_id=TEST_USER_ID, key=key)


@pytest.mark.parametrize("payload", [b"", b"\x01", b"\x01" + b"\x00" * 20])
def test_truncated_payloads_are_rejected(key: bytes, payload: bytes) -> None:
    with pytest.raises(DecryptionError):
        decrypt_password(payload, user_id=TEST_USER_ID, key=key)


def test_unknown_format_version_is_rejected(key: bytes) -> None:
    """Version byte exists so a future format change is detectable (spec 6.7)."""
    payload = bytearray(encrypt_password(Secret(PASSWORD), user_id=TEST_USER_ID, key=key))
    payload[0] = 99
    with pytest.raises(DecryptionError):
        decrypt_password(bytes(payload), user_id=TEST_USER_ID, key=key)


def test_decryption_errors_never_leak_the_plaintext_or_key(key: bytes) -> None:
    payload = encrypt_password(Secret(PASSWORD), user_id=TEST_USER_ID, key=key)
    with pytest.raises(DecryptionError) as excinfo:
        decrypt_password(payload, user_id=OTHER_USER_ID, key=key)
    message = str(excinfo.value)
    assert PASSWORD not in message
    assert key.hex() not in message


@pytest.mark.parametrize("bad_key", [b"", b"\x00" * 16, b"\x00" * 31, b"\x00" * 33])
def test_wrong_key_length_is_a_programming_error(bad_key: bytes) -> None:
    """ValueError, not DecryptionError — the caller is broken, not the data."""
    with pytest.raises(ValueError, match="key must be"):
        encrypt_password(Secret(PASSWORD), user_id=TEST_USER_ID, key=bad_key)


def test_empty_user_id_is_rejected(key: bytes) -> None:
    with pytest.raises(ValueError, match="user_id must not be empty"):
        encrypt_password(Secret(PASSWORD), user_id="", key=key)


def test_empty_password_is_rejected(key: bytes) -> None:
    """Fail here rather than at the Garmin login, far from the cause."""
    with pytest.raises(ValueError, match="password must not be empty"):
        encrypt_password(Secret(""), user_id=TEST_USER_ID, key=key)


def test_decrypt_returns_a_secret_that_stays_redacted(key: bytes) -> None:
    payload = encrypt_password(Secret(PASSWORD), user_id=TEST_USER_ID, key=key)
    recovered = decrypt_password(payload, user_id=TEST_USER_ID, key=key)
    assert PASSWORD not in f"{recovered!r} {recovered}"
