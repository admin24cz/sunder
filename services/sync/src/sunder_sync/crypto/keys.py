"""Loading and validating the AES-256 master key.

Spec section 6.1 and 6.3: the key lives in a GitHub Secret, is available only to
the sync workflow, and is never written to the database or the repository. This
module is the single place that turns the `ENCRYPTION_KEY` environment variable
into usable bytes, so all validation happens once and consistently.
"""

from __future__ import annotations

import binascii
import os
from collections.abc import Mapping

from sunder_sync.crypto.errors import InvalidEncryptionKeyError, MissingEncryptionKeyError

ENCRYPTION_KEY_ENV = "ENCRYPTION_KEY"
"""Name of the environment variable holding the hex-encoded master key."""

KEY_BYTES = 32
"""AES-256 key length. Generate one with `openssl rand -hex 32`."""

_KEY_HEX_CHARS = KEY_BYTES * 2


def parse_key(raw: str) -> bytes:
    """Decode a hex-encoded master key into raw bytes.

    Args:
        raw: 64 hex characters. Surrounding whitespace is tolerated because it
            is trivially introduced by copy-paste and by shell heredocs.

    Returns:
        Exactly `KEY_BYTES` bytes of key material.

    Raises:
        MissingEncryptionKeyError: If `raw` is empty or only whitespace.
        InvalidEncryptionKeyError: If `raw` is not valid hex of the right
            length. The message describes the shape of the problem and never
            includes any part of the value.
    """
    candidate = raw.strip()
    if not candidate:
        raise MissingEncryptionKeyError(
            f"{ENCRYPTION_KEY_ENV} is empty; generate one with `openssl rand -hex 32`"
        )

    if len(candidate) != _KEY_HEX_CHARS:
        raise InvalidEncryptionKeyError(
            f"{ENCRYPTION_KEY_ENV} must be {_KEY_HEX_CHARS} hex characters "
            f"({KEY_BYTES} bytes), got {len(candidate)} characters"
        )

    try:
        key = bytes.fromhex(candidate)
    except (ValueError, binascii.Error) as exc:
        raise InvalidEncryptionKeyError(f"{ENCRYPTION_KEY_ENV} is not valid hexadecimal") from exc

    # Belt and braces: a 64-char hex string always decodes to 32 bytes, but the
    # invariant is cheap to assert and this is the value AES-GCM is built on.
    if len(key) != KEY_BYTES:
        raise InvalidEncryptionKeyError(
            f"{ENCRYPTION_KEY_ENV} decoded to {len(key)} bytes, expected {KEY_BYTES}"
        )

    return key


def load_encryption_key(env: Mapping[str, str] | None = None) -> bytes:
    """Read and validate the master key from the environment.

    Args:
        env: Mapping to read from. Defaults to `os.environ`; injectable so tests
            never have to mutate real process state.

    Returns:
        Exactly `KEY_BYTES` bytes of key material.

    Raises:
        MissingEncryptionKeyError: If the variable is unset or empty. There is
            deliberately no fallback and no generated default — encrypting with
            an ephemeral key would produce database rows that can never be
            decrypted again, and the failure would only surface on the next run.
        InvalidEncryptionKeyError: If the value is malformed.
    """
    source = os.environ if env is None else env
    raw = source.get(ENCRYPTION_KEY_ENV)
    if raw is None:
        raise MissingEncryptionKeyError(
            f"{ENCRYPTION_KEY_ENV} is not set; it must be provided as a GitHub Secret "
            f"(see docs/spec.md section 14)"
        )
    return parse_key(raw)
