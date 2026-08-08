"""Reading a stored credential, whichever format it is in (ADR 0002).

Two formats coexist by design:

* **Version 2, sealed box.** Written by the Edge Function when a user links
  their account. The writer holds only a public key and cannot read it back.
* **Version 1, AES-256-GCM.** Written by the sync service itself, which both
  reads and writes and therefore has no need for asymmetry.

Callers should not have to care which they are holding. The version byte at the
front of the payload makes a stored credential self-describing, so this module
dispatches on it and everything upstream just asks for the password.
"""

from __future__ import annotations

from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from sunder_sync.crypto.credentials import VERSION_AES_256_GCM, decrypt_password
from sunder_sync.crypto.errors import DecryptionError
from sunder_sync.crypto.sealing import VERSION_SEALED_BOX, unseal_password
from sunder_sync.crypto.secret import Secret


@dataclass(frozen=True, slots=True)
class CredentialKeys:
    """The key material a sync run needs to read stored credentials.

    Both are required. A run holding only one could open half the rows and would
    report the other half as corrupt, which is a far more confusing failure than
    refusing to start.
    """

    encryption_key: bytes
    """32-byte AES key, from `ENCRYPTION_KEY`."""

    credential_private_key: X25519PrivateKey
    """X25519 private key, from `CREDENTIAL_PRIVATE_KEY`."""


def open_credential(payload: bytes, *, user_id: str, keys: CredentialKeys) -> Secret:
    """Decrypt a stored credential, whichever format it uses.

    Args:
        payload: The bytes from `garmin_connections.garmin_password_encrypted`.
        user_id: The row's owner. Authenticated into both formats, so a payload
            moved to another row fails here rather than logging into the wrong
            Garmin account.
        keys: Both keys.

    Returns:
        The plaintext password, wrapped in `Secret`.

    Raises:
        DecryptionError: If the payload is empty, carries an unknown version, or
            fails to decrypt under the appropriate key.
    """
    if not payload:
        raise DecryptionError("stored credential is empty")

    version = payload[0]

    if version == VERSION_SEALED_BOX:
        return unseal_password(payload, user_id=user_id, private_key=keys.credential_private_key)

    if version == VERSION_AES_256_GCM:
        return decrypt_password(payload, user_id=user_id, key=keys.encryption_key)

    raise DecryptionError(f"unsupported credential format version {version}")
