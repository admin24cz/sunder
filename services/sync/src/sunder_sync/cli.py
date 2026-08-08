"""Entry point for the sync workflow.

Run by `.github/workflows/sync.yml` on a cron. Kept thin on purpose: it wires
configuration to the runner and translates the result into an exit code, and
everything worth testing lives in the modules it calls.
"""

from __future__ import annotations

import logging
import sys

from sunder_sync.config import ConfigError, SyncConfig
from sunder_sync.db import SyncRepository
from sunder_sync.garmin import GarminClient
from sunder_sync.runner import run_sync

logger = logging.getLogger("sunder_sync")

EXIT_OK = 0
EXIT_CONFIG_ERROR = 1
EXIT_SOME_USERS_FAILED = 2


def configure_logging(*, verbose: bool = False) -> None:
    """Set up logging for a workflow run.

    Deliberately plain: GitHub Actions already timestamps and folds output, and
    a structured formatter would only make the log harder to read there.

    Third-party loggers are pinned to WARNING. `httpx` logs every request at
    INFO including the full URL, and a Supabase URL carries the project ref;
    `garth` is noisier still and sits closest to the credentials.
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    for noisy in ("httpx", "httpcore", "garth", "garminconnect", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> int:
    """Run one sync and return a process exit code.

    Returns:
        `EXIT_OK` when every connection synced, `EXIT_CONFIG_ERROR` when the
        environment is unusable, and `EXIT_SOME_USERS_FAILED` when the run
        completed but at least one user failed.

        The last case is a distinct code rather than a success because spec 7.5
        wants repeated failures to be noticeable — a workflow that stayed green
        while nobody's activities imported is exactly the silent failure the
        sync log exists to prevent. It is also not a hard error: per-user
        isolation means the other users did sync, and that is worth recording as
        a partial success.
    """
    configure_logging()

    # One subcommand, dispatched before anything else: `authorize` is
    # interactive and needs neither the encryption key nor the private key, so
    # it must not be blocked by a config check meant for the sync run.
    if len(sys.argv) > 1 and sys.argv[1] == "authorize":
        from sunder_sync.authorize import run_authorize

        return run_authorize(sys.argv[2:])

    try:
        config = SyncConfig.from_env()
    except ConfigError as exc:
        # No traceback: the message is written to be actionable, and a stack
        # trace here only buries it.
        logger.error("Configuration error: %s", exc)  # noqa: TRY400 - traceback would bury an actionable message
        return EXIT_CONFIG_ERROR

    def build_client() -> GarminClient:
        """Build a Garmin client for one user.

        The import is local so that the test suite, which never reaches this
        function, does not need `garminconnect` importable — and so an import
        failure in that library is reported as a per-user sync failure rather
        than as a crash before the run even starts.
        """
        from garminconnect import Garmin

        return GarminClient(api_factory=Garmin)

    with SyncRepository(
        url=config.supabase_url, service_role_key=config.service_role_key
    ) as repository:
        summary = run_sync(
            repository=repository,
            config=config,
            client_factory=build_client,
        )

    if summary.failed:
        logger.warning(
            "%d of %d connection(s) failed; see sync_runs for details",
            summary.failed,
            summary.users_processed,
        )
        return EXIT_SOME_USERS_FAILED

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
