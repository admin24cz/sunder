"""Tests for `Secret` — the wrapper that keeps credentials out of log output."""

from __future__ import annotations

import logging
import pickle
from collections.abc import Callable

import pytest

from sunder_sync.crypto import Secret

PASSWORD = "correct horse battery staple"


def test_reveal_returns_the_plaintext() -> None:
    assert Secret(PASSWORD).reveal() == PASSWORD


@pytest.mark.parametrize(
    "render",
    [
        str,
        repr,
        lambda s: f"{s}",
        lambda s: f"{s!r}",
        lambda s: f"{s!s}",
        lambda s: f"{s:>60}",  # a format spec must not route around __str__
        lambda s: "{}".format(s),  # noqa: UP032 - str.format is one of the paths under test
        lambda s: "%s" % (s,),  # noqa: UP031 - percent formatting is under test too
        lambda s: "%r" % (s,),  # noqa: UP031 - percent formatting is under test too
    ],
)
def test_plaintext_never_appears_in_rendered_output(render: Callable[[Secret], str]) -> None:
    """Every path from object to text must redact. This is the whole point."""
    assert PASSWORD not in render(Secret(PASSWORD))


def test_plaintext_never_reaches_the_log(caplog: pytest.LogCaptureFixture) -> None:
    """A logging call is the realistic way a password escapes (spec 6.4)."""
    secret = Secret(PASSWORD)
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("sunder.test").info("logging in as %s / %s", "user@example.com", secret)
    assert PASSWORD not in caplog.text
    assert "redacted" in caplog.text


def test_plaintext_never_appears_in_a_traceback() -> None:
    """Tracebacks render locals via repr in many reporting tools."""
    secret = Secret(PASSWORD)
    with pytest.raises(RuntimeError) as excinfo:
        raise RuntimeError(f"login failed for {secret}")
    assert PASSWORD not in str(excinfo.value)


def test_equality_compares_secrets() -> None:
    assert Secret(PASSWORD) == Secret(PASSWORD)
    assert Secret(PASSWORD) != Secret("something else")


def test_comparison_against_a_bare_string_is_not_equal() -> None:
    """Refusing the comparison stops a plaintext from sneaking in as `other`."""
    assert Secret(PASSWORD) != PASSWORD


def test_unhashable_so_it_cannot_land_in_a_dict_key_or_set_repr() -> None:
    with pytest.raises(TypeError):
        hash(Secret(PASSWORD))


def test_unpicklable_so_it_cannot_be_written_to_disk() -> None:
    with pytest.raises(TypeError):
        pickle.dumps(Secret(PASSWORD))


def test_non_string_input_is_rejected_at_construction() -> None:
    with pytest.raises(TypeError):
        Secret(b"bytes are not a password")  # type: ignore[arg-type]


def test_truthiness_and_length_are_available_for_validation() -> None:
    assert not Secret("")
    assert Secret(PASSWORD)
    assert len(Secret(PASSWORD)) == len(PASSWORD)
