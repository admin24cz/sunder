"""Tests for loading and validating the AES-256 master key."""

from __future__ import annotations

import pytest

from sunder_sync.crypto import (
    ENCRYPTION_KEY_ENV,
    KEY_BYTES,
    InvalidEncryptionKeyError,
    MissingEncryptionKeyError,
    load_encryption_key,
    parse_key,
)
from tests.conftest import TEST_KEY, TEST_KEY_HEX


def test_parses_a_valid_hex_key() -> None:
    assert parse_key(TEST_KEY_HEX) == TEST_KEY
    assert len(parse_key(TEST_KEY_HEX)) == KEY_BYTES


def test_tolerates_surrounding_whitespace() -> None:
    """Copy-paste and shell heredocs add newlines; that is not a bad key."""
    assert parse_key(f"  {TEST_KEY_HEX}\n") == TEST_KEY


@pytest.mark.parametrize("raw", ["", "   ", "\n"])
def test_blank_key_is_reported_as_missing_not_invalid(raw: str) -> None:
    with pytest.raises(MissingEncryptionKeyError):
        parse_key(raw)


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(TEST_KEY_HEX[:-2], id="too-short"),
        pytest.param(TEST_KEY_HEX + "ff", id="too-long"),
        pytest.param("z" * 64, id="not-hexadecimal"),
        pytest.param(TEST_KEY_HEX[:-1] + "g", id="one-bad-character"),
    ],
)
def test_malformed_keys_are_rejected(raw: str) -> None:
    with pytest.raises(InvalidEncryptionKeyError):
        parse_key(raw)


@pytest.mark.parametrize("raw", [TEST_KEY_HEX[:-2], "z" * 64, "short"])
def test_error_messages_never_echo_the_key(raw: str) -> None:
    """A truncated key in a CI log is still most of a key."""
    with pytest.raises(InvalidEncryptionKeyError) as excinfo:
        parse_key(raw)
    assert raw not in str(excinfo.value)


def test_loads_from_an_injected_environment() -> None:
    assert load_encryption_key({ENCRYPTION_KEY_ENV: TEST_KEY_HEX}) == TEST_KEY


def test_unset_variable_raises_rather_than_generating_a_key() -> None:
    """A generated fallback would write rows nobody could ever decrypt again."""
    with pytest.raises(MissingEncryptionKeyError):
        load_encryption_key({})


def test_empty_variable_raises() -> None:
    with pytest.raises(MissingEncryptionKeyError):
        load_encryption_key({ENCRYPTION_KEY_ENV: ""})
