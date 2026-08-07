# Security model

Sunder stores third-party login credentials. That is a heavier obligation than
the rest of the app combined, and this document is where the reasoning lives.

Companion to spec section 6, which states the requirements; this describes how
they are met and what to do when something goes wrong.

---

## The threat we actually design for

**Assume the database leaks.** Not as a remote possibility — as the design
assumption. A free-tier Postgres reachable from the internet, with a service
role key that lives in a CI system, is a realistic thing to lose.

Under that assumption:

* Training data is exposed. Unpleasant, not dangerous.
* Garmin passwords must remain unreadable. **This is the property everything
  else serves.**

The second one holds because the decryption key is never in the database. An
attacker with a full dump has ciphertext and nothing to open it with.

## What is not defended against

Stating this plainly matters more than an exhaustive list of what is.

* **A compromised GitHub Actions environment.** The sync job legitimately holds
  the encryption key and the service role key. Anyone who can run arbitrary code
  there can decrypt every credential. This is why the repository's Actions
  secrets, and who can open a pull request that runs against them, are the most
  sensitive access control in the project.
* **A malicious or compromised dependency in the sync service.** `garth` and
  `python-garminconnect` receive plaintext passwords by necessity.
* **Garmin itself.** Passwords are sent to Garmin, which is the point.
* **A user who reuses their Garmin password elsewhere.** Nothing here helps.

---

## Credential encryption

Implemented in `services/sync/src/sunder_sync/crypto/`.

**AES-256-GCM.** Authenticated encryption, so a tampered row fails to decrypt
rather than yielding a plausible wrong password that would then be typed into
Garmin's login form and count against the account's failed-attempt limit.

**Stored payload:**

```
┌─────────┬──────────┬───────────────────────────┐
│ version │ nonce    │ ciphertext ‖ GCM tag      │
│ 1 byte  │ 12 bytes │ len(plaintext) + 16 bytes │
└─────────┴──────────┴───────────────────────────┘
```

**The user id is authenticated as associated data.** The ciphertext is bound to
the row it lives in. Someone with write access to the database cannot move
Alice's encrypted password onto Bob's connection and have the sync service log
into Alice's Garmin account during Bob's run.

**A fresh random nonce per encryption.** Nonce reuse under one key is the single
catastrophic failure of GCM — it leaks the XOR of the plaintexts and the
authentication subkey. There is no counter to share safely across concurrent
workflow runs, so 96 random bits per write is the right construction.

**Decryption failures are indistinguishable.** Wrong key, corrupted ciphertext,
wrong user, unknown version — all raise the same `DecryptionError` with the same
message. Distinguishing them would tell someone holding a stolen database
whether a guessed key was close.

**The version byte.** Key rotation and any future algorithm change need to
distinguish old payloads from new ones. One byte now; unguessable later.

## Keeping plaintext out of logs

`Secret` (`crypto/secret.py`) wraps every plaintext password. It redacts in
`repr`, `str`, f-strings, `%` formatting, `logging` calls and tracebacks, refuses
to hash, and refuses to pickle. `reveal()` is the only way out.

The reason it exists rather than a rule in a style guide: "never log the
password" depends on every author remembering, at every call site, forever. One
f-string in an error path is enough. Inverting the default makes disclosure a
deliberate, greppable act.

This is **not** memory protection. CPython strings are immutable and cannot be
zeroed; the plaintext survives until garbage collection. The threat being
addressed is accidental disclosure through output, which is the one that
actually happens.

---

## Row Level Security

Defined in `supabase/migrations/20260807090100_rls_policies.sql`.

**Deny-first ordering.** The migration that creates the tables enables RLS and
defines no policies. RLS with no policies is deny-all, so between the two
migrations nothing is reachable. Policies are then opened one at a time. There is
no moment at which a table exists but is readable.

**`garmin_connections` has no policy at all**, and its grants are revoked from
`anon` and `authenticated`. Two independent mistakes would be needed to expose
it, not one. Credentials are written by an Edge Function using the service role.

**Computed tables are read-only for clients.** Activities, segment efforts and
personal records have no insert or update policy — they come from the sync
service. A stolen anon key plus a valid session still cannot fabricate training
history.

**Policies name `authenticated`, never `public`.** The anon role has no
legitimate read anywhere in this schema.

Verified by `services/sync/tests/security/test_rls.py` against a real Postgres.
A mocked test would only prove the mock agrees with our assumptions.

---

## Key and secret handling

| Secret | Lives in | Must never be in |
|---|---|---|
| `SUPABASE_ANON_KEY` | frontend bundle, public | — (public by design) |
| `SUPABASE_SERVICE_ROLE_KEY` | GitHub Secrets | frontend, repo, logs |
| `SUPABASE_DB_URL` | GitHub Secrets | frontend, repo, logs |
| `ENCRYPTION_KEY` | GitHub Secrets | frontend, repo, logs, **database** |
| `BACKUP_ENCRYPTION_KEY` | GitHub Secrets | frontend, repo, logs |

Three independent mechanisms enforce this:

1. **gitleaks**, as a pre-commit hook and as a CI job with full history — a key
   committed and then removed is still leaked.
2. **A static scan of the frontend build** (`tests/security/`) failing on any
   secret name or value in `apps/web/dist`. Vite inlines every `VITE_*`
   variable, so one mistakenly-prefixed name would publish a service role key to
   every visitor.
3. **`::add-mask::`** on secrets in workflows before any step can echo them.

---

## Key rotation

### Rotating `ENCRYPTION_KEY`

There is no automated rotation yet. The manual procedure:

1. Generate a new key. **Keep the old one** until step 4 completes.
2. Run a one-off script with both keys available: read each
   `garmin_connections` row, `decrypt_password` with the old key,
   `encrypt_password` with the new one, write it back.
3. Update the `ENCRYPTION_KEY` GitHub Secret.
4. Trigger a sync run and confirm every connection still reports `active`.
5. Destroy the old key only after step 4 passes.

The version byte in the payload allows a smarter scheme later — bumping the
version so old and new rows coexist and are migrated lazily. Not needed at
current scale.

### Rotating a Supabase key

Rotating `service_role` in the dashboard invalidates the old one immediately.
Update the GitHub Secret first, then rotate, then re-run the sync workflow to
confirm.

---

## Incident response

### The `ENCRYPTION_KEY` may have leaked

Treat every stored Garmin password as compromised.

1. Set every `garmin_connections.status` to `disabled` so no sync uses them.
2. Tell affected users to change their Garmin password **immediately** — the
   ciphertext is now decryptable by whoever holds the key.
3. Generate a new key and update the secret.
4. Delete every credential row. Users re-link with their new passwords.
5. Do not attempt to re-encrypt the old values. They are compromised plaintext.

### The service role key may have leaked

1. Rotate it in the Supabase dashboard. This takes effect at once.
2. Update the GitHub Secret and re-run the sync workflow.
3. Review the Supabase logs for what was accessed while it was valid.
4. Credentials remain encrypted — a service role key alone does not decrypt
   them. This is exactly the separation the design buys.

### The database may have leaked

1. Training data is exposed; notify affected users.
2. Passwords stay unreadable **as long as `ENCRYPTION_KEY` did not leak with
   it**. Confirm that separately, and treat the two as one incident if the
   attacker had access to the Actions environment.
3. Rotate the database password and every Supabase key.

### Garmin blocked an account

Expected failure mode, not a security incident (see spec section 2). The sync
marks the connection `rate_limited` or `auth_failed` and skips it. Do not retry
in a loop; that is what caused it.

---

## Deliberately deferred

Listed so they are decisions rather than oversights.

* **Audit log for `garmin_connections` access** (spec 6.7). Worth adding before
  the app is opened beyond a handful of trusted users.
* **2FA on the Sunder account.** Supabase Auth supports it. Not enabled while
  the instance is single-user.
* **Automated key rotation.** The manual procedure above suffices at this scale.
* **Rate limiting on the linking Edge Function.** Needed before public signup is
  enabled.
