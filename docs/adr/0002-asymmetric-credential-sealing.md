# ADR 0002 — Seal credentials with a public key at the point of entry

**Status:** accepted
**Date:** 2026-08-08
**Deviates from:** `docs/spec.md` sections 6.1 and 6.3

## Context

Two requirements in the specification cannot both be met as written.

* **Section 6.3** states that `ENCRYPTION_KEY` may exist only as a GitHub Secret,
  available to the sync workflow, and never in the database or anywhere else.
* **Section 6.5** states that the Garmin linking form submits to a Supabase Edge
  Function, which stores the credential.

The Edge Function has to encrypt the password before writing it. With one
symmetric key, that means the key must also be a Supabase secret — and the whole
point of section 6.1 is that the key which opens the ciphertext should not live
next to the ciphertext. Supabase would then hold both halves, and the guarantee
"a database leak alone exposes nothing" would depend on Supabase's secret store
being a genuinely separate compartment from Supabase's database. That is a much
weaker claim than the one the specification is trying to make.

Three ways out were considered:

1. **Give the Edge Function the symmetric key.** Simplest, and what most projects
   do. It also silently downgrades the property section 6.1 exists to provide.
2. **Have the browser encrypt before submitting.** Moves the key into the
   frontend bundle, which is public. Strictly worse.
3. **Use asymmetric encryption.** The writer needs only a public key; only the
   holder of the private key can read.

## Decision

Credentials are sealed to a public key at the point of entry, using libsodium's
sealed box construction (X25519 key exchange with XSalsa20-Poly1305).

| Key | Lives in | Can |
|---|---|---|
| `CREDENTIAL_PUBLIC_KEY` | Supabase Edge Function config, frontend-adjacent, not secret | encrypt only |
| `CREDENTIAL_PRIVATE_KEY` | GitHub Secret, sync workflow only | decrypt |

The public key is not a secret at all, which removes the whole question of
whether Supabase should be trusted to hold it. A sealed box also generates an
ephemeral keypair per message, so the ciphertext leaks nothing about the sender
and there is no nonce for the Edge Function to get wrong.

`ENCRYPTION_KEY` and the AES-256-GCM layer are **retained**, unchanged, for
everything the sync service itself writes. The two coexist deliberately:

* **Sealing (asymmetric)** is for the *write* path, where the writer must not be
  able to read what it wrote.
* **AES-256-GCM (symmetric)** is for re-encryption after the sync service has
  decrypted a credential, and for key rotation, where the same component both
  reads and writes.

The version byte already present in the payload format distinguishes the two, so
a stored credential is self-describing and rotation between them needs no
migration.

## Consequences

**The property section 6.1 wants now actually holds.** An attacker with the full
Supabase project — database, storage, Edge Function configuration and all — has
ciphertext and a public key. Neither opens anything. Previously this required
trusting that two parts of Supabase were separate compartments.

**One more secret to manage.** Section 14's checklist gains
`CREDENTIAL_PRIVATE_KEY` and `CREDENTIAL_PUBLIC_KEY`, documented in
`docs/setup.md` with generation instructions.

**Losing the private key is still unrecoverable** — the same warning as
`ENCRYPTION_KEY`, and for the same reason. Users would have to re-link.

**A sealed credential cannot be verified at rest.** With the symmetric scheme,
the component that wrote a credential could read it back and confirm it. It no
longer can, by design. The linking flow therefore validates the credential by
attempting a Garmin login *before* sealing it, rather than after storing it.

**The specification is not rewritten.** Sections 6.1 and 6.3 remain the record of
the original reasoning; this ADR is the amendment, per the principle in section
8.4 that history is added to rather than edited.
