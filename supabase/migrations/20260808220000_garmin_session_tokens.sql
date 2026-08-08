-- Store Garmin session tokens instead of a password (ADR 0003).
--
-- WHY: the first real login revealed that an account with two-factor
-- authentication cannot be used at all by an unattended sync. Garmin accepts
-- the password and then demands a one-time code, which a cron job has nowhere
-- to obtain.
--
-- The fix is to authenticate once, interactively, and keep the OAuth tokens
-- that login produces. The sync then resumes that session rather than logging
-- in at all.
--
-- This is a security improvement on its own terms, independent of MFA. Spec
-- section 9 is uneasy about holding other people's Garmin passwords, and with
-- good reason: a password unlocks the whole account and is very often reused
-- elsewhere. A session token unlocks Garmin Connect, expires, and can be
-- revoked from Garmin's own device list without changing anything else.

alter table public.garmin_connections
  add column garmin_tokens_encrypted bytea;

comment on column public.garmin_connections.garmin_tokens_encrypted is
  'Sealed OAuth1 + OAuth2 tokens from an interactive Garmin login. Same sealed '
  'box format as the password column (ADR 0002): only CREDENTIAL_PRIVATE_KEY '
  'opens it, and the component that wrote it cannot read it back.';

-- WHY the password column becomes nullable rather than being dropped: a
-- connection created before this migration still has one, and dropping the
-- column would destroy the only credential those users have without warning.
-- The sync prefers tokens and falls back to the password, so both kinds work
-- while accounts migrate over.
--
-- Once every row has tokens, the column should be dropped — holding a password
-- that is no longer used is pure liability. That is a deliberate follow-up, not
-- something to do in the same migration that introduces the alternative.
alter table public.garmin_connections
  alter column garmin_password_encrypted drop not null;

-- A row with neither credential is meaningless and would fail every sync run
-- with a confusing error. Rejecting it here means the failure surfaces at the
-- write, where the cause is obvious.
alter table public.garmin_connections
  add constraint garmin_connections_has_a_credential
  check (
    garmin_password_encrypted is not null
    or garmin_tokens_encrypted is not null
  );

-- Lets the sync select token-based connections without reading the ciphertext.
comment on constraint garmin_connections_has_a_credential
  on public.garmin_connections is
  'A connection must carry either a sealed password or sealed session tokens.';
