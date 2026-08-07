"""A string wrapper that resists ending up in logs.

Spec section 6.4 forbids logging passwords and tokens. Relying on every call
site to remember that is a losing bet — one f-string in an error path is enough.
`Secret` inverts the default: the value is invisible unless someone explicitly
asks for it with `.reveal()`, so leaking it becomes a visible, greppable act
rather than an accident.
"""

from __future__ import annotations

import hmac
from typing import Any, final

_REDACTED = "***redacted***"


@final
class Secret:
    """Holds a sensitive string and keeps it out of textual output.

    Redacted in `repr()`, `str()`, f-strings and therefore in logging calls and
    exception tracebacks. `reveal()` is the only way out.

    Example:
        >>> password = Secret("hunter2")
        >>> f"logging in with {password}"
        'logging in with ***redacted***'
        >>> password.reveal()
        'hunter2'

    Note:
        This is not memory protection. CPython strings are immutable and cannot
        be zeroed, so the plaintext lives until it is garbage collected; see
        `sunder_sync.crypto.credentials` for how call sites keep that window
        short. The threat this class addresses is accidental disclosure through
        output, which is the one that actually happens in practice.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        """Wrap `value`.

        Raises:
            TypeError: If `value` is not a `str`. Wrapping bytes or `None` by
                mistake would otherwise produce a `Secret` that reveals
                something unusable much later, far from the cause.
        """
        if not isinstance(value, str):
            raise TypeError(f"Secret expects str, got {type(value).__name__}")
        self._value = value

    def reveal(self) -> str:
        """Return the wrapped plaintext.

        The single intentional exit point. Call it as late as possible and do
        not bind the result to a long-lived name.
        """
        return self._value

    def __repr__(self) -> str:
        """Return a redacted representation (used by tracebacks and `%r`)."""
        return f"Secret({_REDACTED})"

    def __str__(self) -> str:
        """Return a redacted string (used by f-strings and `print`)."""
        return _REDACTED

    def __format__(self, format_spec: str) -> str:
        """Return a redacted string, ignoring any format spec.

        Overridden so that `f"{secret:>40}"` cannot pad its way around `__str__`
        and reveal padding-derived length information.
        """
        del format_spec
        return _REDACTED

    def __eq__(self, other: object) -> bool:
        """Compare two secrets in constant time.

        Constant-time so that comparing a `Secret` against attacker-supplied
        input cannot leak the value's prefix through timing. Anything that is
        not a `Secret` is unequal — comparing against a bare `str` is refused
        rather than silently doing the unsafe thing.
        """
        if not isinstance(other, Secret):
            return NotImplemented
        return hmac.compare_digest(self._value, other._value)

    def __hash__(self) -> int:
        """Raise: hashing would put the value into dict keys and set reprs."""
        raise TypeError("Secret is unhashable to keep its value out of collections")

    def __bool__(self) -> bool:
        """Return whether the wrapped string is non-empty."""
        return bool(self._value)

    def __len__(self) -> int:
        """Return the plaintext length.

        Exposed because callers legitimately need to validate that a password is
        non-empty before attempting a Garmin login.
        """
        return len(self._value)

    def __reduce__(self) -> Any:  # noqa: ANN401 - pickle protocol signature
        """Raise: pickling would write the plaintext to disk or over a socket."""
        raise TypeError("Secret cannot be pickled")
