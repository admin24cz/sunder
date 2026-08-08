# ADR 0003 — Store Garmin session tokens instead of passwords

**Status:** accepted
**Date:** 2026-08-08
**Deviates from:** `docs/spec.md` sections 5.1, 6.1 and 8.1

## Context

The first sync against a real Garmin account failed. The specification assumes
throughout that an email and a password are enough — section 5.1 describes the
user entering credentials, 6.1 describes encrypting the password, and 8.1 has a
`garmin_password_encrypted` column.

That assumption does not survive contact with an account that has two-factor
authentication enabled. Garmin accepted the password and then demanded a
one-time code:

```
GarminConnectAuthenticationError: MFA Required but no prompt_mfa mechanism supplied
caused by: _MFARequired
```

A cron job has nowhere to obtain that code. No amount of retrying, backoff or
error handling changes it: the credential the specification tells us to store is
simply not sufficient to log in.

Two ways forward were available.

**Turn the second factor off.** Immediate, no code changes — and it asks the
user to weaken the security of an account we do not own, to work around a
limitation of ours. It also leaves the password stored, which is the thing
section 9 is uneasy about.

**Authenticate once interactively and keep the session.** Garmin's login
produces OAuth1 and OAuth2 tokens; `garth` can serialise them, and
`garminconnect` can resume from them without logging in at all.

## Decision

Sunder stores **session tokens**, not passwords.

A new command authenticates once, with a human present to supply the code:

```
uv run python -m sunder_sync.cli authorize <user-id>
```

The password is typed, used, and discarded. What is written to the database is
the serialised token pair, sealed to `CREDENTIAL_PUBLIC_KEY` exactly as a
password would have been (ADR 0002). The stored password, if the connection had
one, is cleared in the same write.

`garmin_connections` gains `garmin_tokens_encrypted`, and
`garmin_password_encrypted` becomes nullable, with a CHECK requiring at least
one of the two. The sync prefers tokens and falls back to a password, so
connections created before this change keep working.

## Consequences

**MFA accounts work.** The problem this started from.

**We stop holding people's passwords, and that is the larger win.** Section 9
worries about the liability of holding third-party credentials, and it is right
to: a password unlocks the entire Garmin account, is often reused elsewhere, and
its compromise is the user's problem long after they stop using Sunder. A
session token unlocks Garmin Connect, expires on its own, and can be revoked by
the user from Garmin's own device list without changing anything else. If the
database and the private key both leaked tomorrow, the blast radius is now
"someone can read your activities until you revoke a session" rather than
"someone has your password".

**Linking is no longer purely self-service.** The web form can still collect a
password, but an MFA account needs the terminal command. That is a real
regression in convenience and it is the cost of the above. A future browser flow
could preserve self-service by prompting for the code in the UI and calling an
Edge Function that performs the login — the sealing and storage are unchanged,
only the prompt moves.

**Tokens expire.** Roughly a year, and sooner if the user revokes the session.
Expiry surfaces as `auth_failed` with a message naming re-authorisation, which
is accurate: re-running `authorize` is exactly the fix.

**The password column should eventually be dropped.** It is nullable now so
existing connections keep working. Once every row carries tokens, keeping a
column that can hold a password is pure liability, and removing it is a
deliberate follow-up.

## Related findings

Two things surfaced while diagnosing this and are recorded here because they
shape what to expect from the sync.

**Garmin rate limits the mobile login endpoints by IP**, returning 429 from a
GitHub Actions runner *and* from a residential connection. `garminconnect` falls
back to a slower browser-like transport, so login still succeeds — but it means
login is the fragile part of the flow, and it is another reason to do it once
rather than hourly. Resuming a session makes no login request at all.

**`garth` is deprecated and no longer maintained** (its own README says so),
while `garminconnect` still depends on it. This is exactly the risk spec section
7.5 anticipates. Nothing to do today; worth watching, and the `GarminApi`
protocol exists so that replacing the library is a change in one module.
