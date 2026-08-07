"""AES-256-GCM encryption of Garmin passwords (spec section 6.1).

Payload format, stored verbatim in `garmin_connections.garmin_password_encrypted`:

    ┌─────────┬──────────────┬────────────────────────────┐
    │ version │ nonce        │ ciphertext ‖ GCM tag       │
    │ 1 byte  │ 12 bytes     │ len(plaintext) + 16 bytes  │
    └─────────┴──────────────┴────────────────────────────┘

Design notes, in the order they matter:

*   **AES-256-GCM, not AES-CBC or Fernet.** GCM is authenticated: a database
    row tampered with by someone who cannot forge the tag fails to decrypt
    instead of yielding a plausible wrong password that would then be typed
    into Garmin's login form.

*   **The user id is authenticated as associated data.** The ciphertext is bound
    to the row it belongs to, so an attacker with write access to the database
    cannot move Alice's encrypted password onto Bob's row and have the sync
    service log into Alice's Garmin account as part of Bob's run. The AAD is
    authenticated but not encrypted — it costs nothing in storage.

*   **A leading version byte.** Key rotation and any future algorithm change
    (spec 6.7) need a way to tell old payloads from new ones. Adding this at the
    start costs one byte; retrofitting it later would mean guessing at the
    format of rows already in production.

*   **A fresh random nonce per encryption.** Nonce reuse under one key is the
    single catastrophic failure mode of GCM — it leaks the XOR of plaintexts and
    the authentication subkey. 96 random bits per write is the construction the
    NIST guidance recommends for exactly this "no shared counter" situation.
"""

from __future__ import annotations

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from sunder_sync.crypto.errors import DecryptionError
from sunder_sync.crypto.keys import KEY_BYTES
from sunder_sync.crypto.secret import Secret

VERSION_AES_256_GCM = 1
"""Payload format version 1: AES-256-GCM with a 12-byte nonce."""

NONCE_BYTES = 12
"""96-bit nonce — the size AES-GCM is optimised for and NIST recommends."""

_TAG_BYTES = 16
_HEADER_BYTES = 1 + NONCE_BYTES
_MIN_PAYLOAD_BYTES = _HEADER_BYTES + _TAG_BYTES

_AAD_PREFIX = b"sunder:garmin-password:v1:"


def _associated_data(user_id: str) -> bytes:
    """Build the authenticated-but-unencrypted context for a payload.

    Includes a domain-separating prefix alongside the user id so that a
    ciphertext produced here can never authenticate under a different purpose we
    might add later (a backup key, an OAuth token) that happens to use the same
    master key.
    """
    return _AAD_PREFIX + user_id.encode("utf-8")


def encrypt_password(password: Secret, *, user_id: str, key: bytes) -> bytes:
    """Encrypt a Garmin password for storage.

    Args:
        password: The plaintext, wrapped so it cannot be logged by accident.
        user_id: Supabase user id owning the credential. Bound into the payload
            as associated data, so the result only decrypts on this user's row.
        key: 32-byte master key from `load_encryption_key`.

    Returns:
        The full payload — version byte, nonce, ciphertext and tag — ready to be
        written to `garmin_connections.garmin_password_encrypted`.

    Raises:
        ValueError: If the key is the wrong length, the user id is empty, or the
            password is empty. Empty inputs are rejected here rather than
            producing a valid-looking payload that fails at Garmin login time.
    """
    if len(key) != KEY_BYTES:
        raise ValueError(f"key must be {KEY_BYTES} bytes, got {len(key)}")
    if not user_id:
        raise ValueError("user_id must not be empty; it is authenticated into the payload")
    if not password:
        raise ValueError("password must not be empty")

    nonce = os.urandom(NONCE_BYTES)
    sealed = AESGCM(key).encrypt(
        nonce,
        password.reveal().encode("utf-8"),
        _associated_data(user_id),
    )
    return bytes([VERSION_AES_256_GCM]) + nonce + sealed


def decrypt_password(payload: bytes, *, user_id: str, key: bytes) -> Secret:
    """Decrypt a stored Garmin password.

    Args:
        payload: The bytes read from `garmin_password_encrypted`.
        user_id: Supabase user id of the row the payload came from. Must match
            the id used at encryption time or authentication fails.
        key: 32-byte master key from `load_encryption_key`.

    Returns:
        The plaintext password, wrapped in `Secret`. Reveal it as late as
        possible — ideally directly at the Garmin login call — and let it go out
        of scope immediately afterwards (spec 6.4).

    Raises:
        ValueError: If the key is the wrong length or the user id is empty.
            These are programming errors, not data problems, so they are
            distinguishable from a decryption failure.
        DecryptionError: If the payload is truncated, uses an unknown format
            version, was written for a different user, was tampered with, or was
            encrypted under a different key. All of these collapse into one
            error on purpose: telling the caller which one it was would help
            somebody holding a stolen database narrow down a key.
    """
    if len(key) != KEY_BYTES:
        raise ValueError(f"key must be {KEY_BYTES} bytes, got {len(key)}")
    if not user_id:
        raise ValueError("user_id must not be empty; it is authenticated into the payload")

    if len(payload) < _MIN_PAYLOAD_BYTES:
        raise DecryptionError("stored credential is truncated")

    version = payload[0]
    if version != VERSION_AES_256_GCM:
        raise DecryptionError(f"unsupported credential format version {version}")

    nonce = payload[1:_HEADER_BYTES]
    sealed = payload[_HEADER_BYTES:]

    try:
        plaintext = AESGCM(key).decrypt(nonce, sealed, _associated_data(user_id))
    except InvalidTag as exc:
        # Chained without the original message: `InvalidTag` carries none, but
        # `raise ... from` keeps the traceback useful without adding detail an
        # attacker could use as an oracle.
        raise DecryptionError("stored credential failed authentication") from exc

    try:
        return Secret(plaintext.decode("utf-8"))
    except UnicodeDecodeError as exc:
        # Authentication passed but the bytes are not text — the key is right and
        # the payload is intact, so this means something wrote a non-password
        # into the column.
        raise DecryptionError("decrypted credential is not valid UTF-8") from exc
