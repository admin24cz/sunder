"""Shared pytest fixtures for the sync service test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

# A fixed, obviously-fake key. Hardcoding it keeps encryption tests
# deterministic; it is never used against a real database.
TEST_KEY_HEX = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
TEST_KEY = bytes.fromhex(TEST_KEY_HEX)

TEST_USER_ID = "11111111-1111-4111-8111-111111111111"
OTHER_USER_ID = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def key() -> bytes:
    """Return a deterministic 32-byte master key for encryption tests."""
    return TEST_KEY


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Return the repository root, resolved from this file's location."""
    # tests/conftest.py -> tests -> sync -> services -> repo root
    return Path(__file__).resolve().parents[3]
