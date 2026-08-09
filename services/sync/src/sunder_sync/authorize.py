"""Interactive Garmin authorisation (ADR 0003).

The one place a human is required. Garmin's second factor is a one-time code, so
somebody has to be present exactly once; after that the sync resumes the session
those credentials bought and never logs in again.

Run it as:

    uv run python -m sunder_sync.cli authorize <user-id>

What it does, and deliberately does not do:

*   The password is typed here, used once, and **never stored**. What is stored
    is the OAuth tokens the login returns, sealed to the credential public key
    exactly like a password would have been (ADR 0002).
*   The stored password, if the account had one, is cleared in the same write.
    A credential kept "just in case" is a credential that outlives its purpose.
"""

from __future__ import annotations

import getpass
import logging
import sys
from collections.abc import Callable

from sunder_sync.crypto import Secret, parse_public_key, seal_password
from sunder_sync.db import SyncRepository
from sunder_sync.garmin import capture_library_diagnostics, classify_exception

logger = logging.getLogger(__name__)

type Prompt = Callable[[str], str]
type SecretPrompt = Callable[[str], str]


class AuthorizationError(RuntimeError):
    """Authorisation could not be completed.

    Messages describe the step that failed. They never contain the password, the
    one-time code, or the tokens.
    """


def authorize(
    user_id: str,
    *,
    repository: SyncRepository,
    credential_public_key_base64: str,
    email: str | None = None,
    prompt: Prompt = input,
    secret_prompt: SecretPrompt = getpass.getpass,
) -> str:
    """Log in to Garmin interactively and store the resulting session.

    Args:
        user_id: The Sunder account the connection belongs to. The tokens are
            sealed against it, so a payload copied onto another row will not
            open (ADR 0002).
        repository: Database access, using the service role.
        credential_public_key_base64: The public half of the credential keypair.
            Sealing needs only this; nothing here can read back what it writes.
        email: Garmin account email. Prompted for when omitted.
        prompt: Reads a visible line. Injectable for tests.
        secret_prompt: Reads a line without echoing. Injectable for tests.

    Returns:
        The Garmin email that was authorised, for the caller to report.

    Raises:
        AuthorizationError: If any step fails. The underlying Garmin failure is
            classified first, so a rate limit or a bad password is named as
            what it is rather than as a generic failure.
    """
    # Imported here rather than at module scope so the test suite, which never
    # runs this function, does not need the Garmin libraries importable.
    from garminconnect import Garmin

    public_key = parse_public_key(credential_public_key_base64)

    garmin_email = email or prompt("Garmin e-mail: ").strip()
    if not garmin_email:
        raise AuthorizationError("no Garmin email given")

    password = Secret(secret_prompt("Garmin password (not stored): "))
    if not password:
        raise AuthorizationError("no Garmin password given")

    def prompt_mfa() -> str:
        """Called by the library only when Garmin asks for a second factor."""
        code = prompt("Garmin MFA code: ").strip()
        if not code:
            raise AuthorizationError("no MFA code given")
        return code

    try:
        with capture_library_diagnostics() as diagnostics:
            api = Garmin(garmin_email, password.reveal(), prompt_mfa=prompt_mfa)
            api.login()
    except AuthorizationError:
        raise
    except Exception as exc:
        # The libraries log why they failed and then raise something generic, so
        # the captured messages are what makes the reported reason accurate.
        # `from None`: the original traceback can hold the request that carried
        # the password.
        reason = classify_exception(exc, diagnostics=diagnostics)
        raise AuthorizationError(f"Garmin login failed: {reason}") from None
    finally:
        # The plaintext has served its only purpose. It is not stored, and this
        # ends its reachability as early as CPython allows.
        del password

    try:
        tokens = Secret(api.garth.dumps())
    except Exception as exc:
        raise AuthorizationError(
            f"logged in, but could not serialise the session ({type(exc).__name__})"
        ) from None

    sealed = seal_password(tokens, user_id=user_id, public_key=public_key)
    del tokens

    repository.store_tokens(user_id, sealed)
    logger.info("Stored Garmin session tokens for %s", garmin_email)
    return garmin_email


def run_authorize(argv: list[str]) -> int:
    """Command-line entry point for `authorize`.

    Kept separate from `authorize` so the logic stays testable without a
    terminal, and so the CLI owns all the printing.
    """
    import os

    if not argv:
        print("usage: python -m sunder_sync.cli authorize <user-id>", file=sys.stderr)
        return 2

    user_id = argv[0]
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    public_key = os.environ.get("CREDENTIAL_PUBLIC_KEY", "").strip()

    missing = [
        name
        for name, value in (
            ("SUPABASE_URL", url),
            ("SUPABASE_SERVICE_ROLE_KEY", service_role_key),
            ("CREDENTIAL_PUBLIC_KEY", public_key),
        )
        if not value
    ]
    if missing:
        print(f"Missing environment: {', '.join(missing)}", file=sys.stderr)
        return 1

    print(
        "Authorising a Garmin account.\n"
        "Your password is used once to obtain a session and is NOT stored.\n"
    )

    try:
        with SyncRepository(url=url, service_role_key=service_role_key) as repository:
            garmin_email = authorize(
                user_id,
                repository=repository,
                credential_public_key_base64=public_key,
            )
    except AuthorizationError as exc:
        print(f"\nFailed: {exc}", file=sys.stderr)
        return 1

    print(f"\nAuthorised {garmin_email}. The next sync will use the stored session.")
    return 0
