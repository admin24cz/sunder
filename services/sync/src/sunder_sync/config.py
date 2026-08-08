"""Configuration for a sync run, read from the environment.

Every value is validated up front and the run refuses to start if anything is
missing. Failing before the first Garmin request is much better than failing
halfway through: a partial run has already spent requests against Garmin's
tolerance, and a missing encryption key discovered at that point would leave
connections marked failed for a reason that was never their fault.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from sunder_sync.crypto import CredentialKeys, load_encryption_key, parse_private_key

DEFAULT_MAX_ACTIVITIES_PER_USER = 50
"""Ceiling on how many activities one run imports for one user.

Spec 7.2 wants the historical backfill spread across runs rather than done as
one large import. With an hourly cron this still catches up on years of history
within a day, while keeping any single run's request count modest.
"""


class ConfigError(RuntimeError):
    """The environment is missing or malformed.

    Messages name the variable, never its value.
    """


@dataclass(frozen=True, slots=True)
class SyncConfig:
    """Everything one sync run needs."""

    supabase_url: str
    service_role_key: str
    encryption_key: bytes
    credential_private_key: X25519PrivateKey
    max_activities_per_user: int = DEFAULT_MAX_ACTIVITIES_PER_USER

    @property
    def credential_keys(self) -> CredentialKeys:
        """Both keys needed to read a stored credential, in either format."""
        return CredentialKeys(
            encryption_key=self.encryption_key,
            credential_private_key=self.credential_private_key,
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        """Build a configuration from environment variables.

        Args:
            env: Mapping to read from; defaults to `os.environ`. Injectable so
                tests never mutate real process state.

        Raises:
            ConfigError: If a required variable is missing or malformed.
        """
        source = os.environ if env is None else env

        url = source.get("SUPABASE_URL", "").strip().rstrip("/")
        if not url:
            raise ConfigError("SUPABASE_URL is not set (see docs/setup.md)")

        service_role_key = source.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not service_role_key:
            raise ConfigError("SUPABASE_SERVICE_ROLE_KEY is not set (see docs/setup.md)")

        try:
            encryption_key = load_encryption_key(source)
        except Exception as exc:
            # Re-raised as ConfigError so the CLI has one failure type to catch;
            # the crypto layer's message is already safe to show.
            raise ConfigError(str(exc)) from exc

        raw_private = source.get("CREDENTIAL_PRIVATE_KEY", "").strip()
        if not raw_private:
            raise ConfigError("CREDENTIAL_PRIVATE_KEY is not set (see docs/setup.md and ADR 0002)")
        try:
            credential_private_key = parse_private_key(raw_private)
        except Exception as exc:
            raise ConfigError(str(exc)) from exc

        raw_max = source.get("SUNDER_MAX_ACTIVITIES_PER_USER", "").strip()
        max_activities = DEFAULT_MAX_ACTIVITIES_PER_USER
        if raw_max:
            try:
                max_activities = int(raw_max)
            except ValueError as exc:
                raise ConfigError("SUNDER_MAX_ACTIVITIES_PER_USER must be an integer") from exc
            if max_activities < 1:
                raise ConfigError("SUNDER_MAX_ACTIVITIES_PER_USER must be at least 1")

        return cls(
            supabase_url=url,
            service_role_key=service_role_key,
            encryption_key=encryption_key,
            credential_private_key=credential_private_key,
            max_activities_per_user=max_activities,
        )
