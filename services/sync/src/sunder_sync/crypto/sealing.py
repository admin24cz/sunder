"""Asymmetric credential sealing (ADR 0002).

The write path for Garmin credentials. A sealed box lets the Supabase Edge
Function encrypt a password it can never read back: it holds only the public
key, while the private key exists solely as a GitHub Secret available to the
sync workflow.

Construction is libsodium's `crypto_box_seal` — X25519 key agreement with an
ephemeral sender keypair, then XSalsa20-Poly1305:

    ┌─────────┬──────────────────────┬───────────────────────────┐
    │ version │ ephemeral public key │ ciphertext ‖ Poly1305 tag │
    │ 1 byte  │ 32 bytes             │ len(plaintext) + 16 bytes │
    └─────────┴──────────────────────┴───────────────────────────┘

The ephemeral keypair is generated per message and its private half is discarded
immediately, which is what makes the sender unable to decrypt its own output —
the property the whole scheme exists for. It also means no nonce has to be
chosen by the caller, removing the one mistake that would be catastrophic.
"""

from __future__ import annotations

import base64

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from sunder_sync.crypto.errors import DecryptionError, InvalidEncryptionKeyError
from sunder_sync.crypto.secret import Secret

VERSION_SEALED_BOX = 2
"""Payload format version 2: sealed to a public key. Version 1 is AES-256-GCM."""

KEY_BYTES = 32
"""X25519 keys are 32 bytes."""

_NONCE_BYTES = 12
_TAG_BYTES = 16
_HEADER_BYTES = 1 + KEY_BYTES
_MIN_PAYLOAD_BYTES = _HEADER_BYTES + _TAG_BYTES

_AAD_PREFIX = b"sunder:garmin-password:sealed:v1:"


def generate_keypair() -> tuple[str, str]:
    """Generate a credential keypair.

    Returns:
        `(private_key_base64, public_key_base64)`. Store the private half as the
        `CREDENTIAL_PRIVATE_KEY` GitHub Secret and hand the public half to the
        Edge Function. The public key is not sensitive.

    Warning:
        Losing the private key makes every sealed credential permanently
        unreadable, exactly as with `ENCRYPTION_KEY`. Keep a copy somewhere
        durable before using it.
    """
    private_key = X25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    public_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    return (
        base64.b64encode(private_bytes).decode("ascii"),
        base64.b64encode(public_bytes).decode("ascii"),
    )


def _decode_key_bytes(encoded: str, name: str) -> bytes:
    """Decode and length-check a base64 X25519 key.

    Raises:
        InvalidEncryptionKeyError: If the value is empty, is not valid base64,
            or is not 32 bytes. The message never echoes any part of the value —
            a partial key in a CI log is still most of a key.
    """
    candidate = encoded.strip()
    if not candidate:
        raise InvalidEncryptionKeyError(f"{name} is empty")

    try:
        raw = base64.b64decode(candidate, validate=True)
    except (ValueError, TypeError) as exc:
        raise InvalidEncryptionKeyError(f"{name} is not valid base64") from exc

    if len(raw) != KEY_BYTES:
        raise InvalidEncryptionKeyError(f"{name} must decode to {KEY_BYTES} bytes, got {len(raw)}")

    return raw


def parse_private_key(encoded: str) -> X25519PrivateKey:
    """Decode a base64 X25519 private key.

    Raises:
        InvalidEncryptionKeyError: If the value is not a valid 32-byte key.
    """
    raw = _decode_key_bytes(encoded, "CREDENTIAL_PRIVATE_KEY")
    try:
        return X25519PrivateKey.from_private_bytes(raw)
    except Exception as exc:
        raise InvalidEncryptionKeyError("CREDENTIAL_PRIVATE_KEY is not a valid X25519 key") from exc


def parse_public_key(encoded: str) -> X25519PublicKey:
    """Decode a base64 X25519 public key.

    Raises:
        InvalidEncryptionKeyError: If the value is not a valid 32-byte key.
    """
    raw = _decode_key_bytes(encoded, "CREDENTIAL_PUBLIC_KEY")
    try:
        return X25519PublicKey.from_public_bytes(raw)
    except Exception as exc:
        raise InvalidEncryptionKeyError("CREDENTIAL_PUBLIC_KEY is not a valid X25519 key") from exc


def _derive_shared_key(
    private_key: X25519PrivateKey, peer_public_key: X25519PublicKey, ephemeral_public: bytes
) -> bytes:
    """Derive the symmetric key for one sealed box.

    The ephemeral public key is mixed into the KDF alongside the shared secret,
    binding the derived key to this specific message. Without it, the same key
    would be derived for every message from the same sender.
    """
    shared_secret = private_key.exchange(peer_public_key)

    digest = hashes.Hash(hashes.BLAKE2s(32))
    digest.update(shared_secret)
    digest.update(ephemeral_public)
    return digest.finalize()


def seal_password(password: Secret, *, user_id: str, public_key: X25519PublicKey) -> bytes:
    """Encrypt a password so that only the private key holder can read it.

    Args:
        password: The plaintext, wrapped so it cannot be logged.
        user_id: Bound in as associated data, so a sealed payload cannot be
            moved onto another user's row.
        public_key: The credential public key.

    Returns:
        The payload to store in `garmin_connections.garmin_password_encrypted`.

    Raises:
        ValueError: If the password or user id is empty.
    """
    if not user_id:
        raise ValueError("user_id must not be empty; it is authenticated into the payload")
    if not password:
        raise ValueError("password must not be empty")

    ephemeral_private = X25519PrivateKey.generate()
    ephemeral_public = ephemeral_private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    key = _derive_shared_key(ephemeral_private, public_key, ephemeral_public)
    # The ephemeral keypair is fresh per message, so the shared key is too and a
    # fixed nonce cannot repeat under it. This is the standard sealed-box
    # argument, and it removes the caller's opportunity to reuse a nonce.
    sealed = ChaCha20Poly1305(key).encrypt(
        b"\x00" * _NONCE_BYTES,
        password.reveal().encode("utf-8"),
        _AAD_PREFIX + user_id.encode("utf-8"),
    )

    # The ephemeral private key goes out of scope here and is never recoverable,
    # which is precisely why the sender cannot decrypt what it just wrote.
    return bytes([VERSION_SEALED_BOX]) + ephemeral_public + sealed


def unseal_password(payload: bytes, *, user_id: str, private_key: X25519PrivateKey) -> Secret:
    """Decrypt a sealed credential.

    Args:
        payload: The bytes read from `garmin_password_encrypted`.
        user_id: The row's owner. Must match the id used when sealing.
        private_key: The credential private key, from the GitHub Secret.

    Returns:
        The plaintext, wrapped in `Secret`.

    Raises:
        ValueError: If the user id is empty — a programming error.
        DecryptionError: If the payload is truncated, is not a sealed box, was
            sealed for a different user, was tampered with, or was sealed to a
            different public key. Collapsed into one error so a stolen database
            offers no oracle.
    """
    if not user_id:
        raise ValueError("user_id must not be empty; it is authenticated into the payload")

    if len(payload) < _MIN_PAYLOAD_BYTES:
        raise DecryptionError("stored credential is truncated")

    if payload[0] != VERSION_SEALED_BOX:
        raise DecryptionError(f"unsupported credential format version {payload[0]}")

    ephemeral_public_bytes = payload[1:_HEADER_BYTES]
    sealed = payload[_HEADER_BYTES:]

    try:
        ephemeral_public = X25519PublicKey.from_public_bytes(ephemeral_public_bytes)
        key = _derive_shared_key(private_key, ephemeral_public, ephemeral_public_bytes)
        plaintext = ChaCha20Poly1305(key).decrypt(
            b"\x00" * _NONCE_BYTES,
            sealed,
            _AAD_PREFIX + user_id.encode("utf-8"),
        )
    except (InvalidTag, ValueError):
        raise DecryptionError("stored credential failed authentication") from None

    try:
        return Secret(plaintext.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise DecryptionError("decrypted credential is not valid UTF-8") from exc
