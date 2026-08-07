"""Encryption of Garmin credentials at rest (spec section 6.1).

Everything a caller needs is re-exported here:

    from sunder_sync.crypto import Secret, load_encryption_key, encrypt_password

The guarantee this package exists to provide: if the Supabase database leaks in
full, the Garmin passwords in it stay unreadable, because the key that decrypts
them is never stored alongside them.
"""

from sunder_sync.crypto.credentials import (
    NONCE_BYTES,
    VERSION_AES_256_GCM,
    decrypt_password,
    encrypt_password,
)
from sunder_sync.crypto.errors import (
    CryptoError,
    DecryptionError,
    InvalidEncryptionKeyError,
    MissingEncryptionKeyError,
)
from sunder_sync.crypto.keys import ENCRYPTION_KEY_ENV, KEY_BYTES, load_encryption_key, parse_key
from sunder_sync.crypto.secret import Secret

__all__ = [
    "ENCRYPTION_KEY_ENV",
    "KEY_BYTES",
    "NONCE_BYTES",
    "VERSION_AES_256_GCM",
    "CryptoError",
    "DecryptionError",
    "InvalidEncryptionKeyError",
    "MissingEncryptionKeyError",
    "Secret",
    "decrypt_password",
    "encrypt_password",
    "load_encryption_key",
    "parse_key",
]
