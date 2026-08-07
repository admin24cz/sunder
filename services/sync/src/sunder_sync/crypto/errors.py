"""Exceptions raised by the crypto package.

Every message here is deliberately free of key material, ciphertext and
plaintext. These exceptions travel into logs and into `sync_runs.errors`, and
spec section 6.4 forbids either from ever containing a credential.
"""


class CryptoError(Exception):
    """Base class for every failure in this package."""


class MissingEncryptionKeyError(CryptoError):
    """The ENCRYPTION_KEY environment variable is absent or empty.

    Raised instead of falling back to a default or generated key: a sync run
    that silently encrypted with the wrong key would produce rows nobody can
    ever decrypt again.
    """


class InvalidEncryptionKeyError(CryptoError):
    """The key is present but is not 32 bytes of valid hex.

    The message never echoes the offending value — a truncated key pasted into
    a log is still most of a key.
    """


class DecryptionError(CryptoError):
    """A stored payload could not be decrypted.

    Causes, none of which are distinguished on purpose: wrong key, corrupted
    ciphertext, a payload written for a different user, or an unknown format
    version. Reporting *which* one would tell an attacker holding the database
    whether a guessed key was close, so the caller only learns that it failed.
    """
