-- Sunder — Row Level Security policies (spec section 6.2).
--
-- Every table already has RLS enabled and no policies, i.e. deny-all. This
-- migration opens exactly the paths the frontend needs and nothing else.
--
-- Two rules shape everything below:
--
--   1. Policies target `authenticated` explicitly, never the default `public`.
--      The anon role has no legitimate read of any row in this schema, so it is
--      never named.
--   2. Read access is owner-scoped via auth.uid(); write access is granted only
--      where the *user* is the author of the data. Everything the sync service
--      computes (activities, efforts, records) stays write-locked to the service
--      role, so a stolen anon key cannot forge training history.
--
-- Verified by the security tests in services/sync/tests/security/ (spec 11.3),
-- not assumed.

-- ---------------------------------------------------------------------------
-- garmin_connections — no policies, ever
-- ---------------------------------------------------------------------------
-- Spec 6.2 is explicit: the frontend gets no access at all, not read, not write.
-- RLS alone would already deny this, but the grants are revoked as well so that
-- a future policy added by mistake still cannot expose the table. Defence in
-- depth: it takes two independent errors to leak credentials, not one.
--
-- Writes happen through an Edge Function with the service role (spec 6.5).

revoke all on public.garmin_connections from anon, authenticated;

-- ---------------------------------------------------------------------------
-- sync_runs — no policies either
-- ---------------------------------------------------------------------------
-- Operational data covering every user. What a single user needs to see — when
-- their own last sync succeeded — is exposed through garmin_connections
-- .last_sync_at by the Edge Function instead, so this table never has to be
-- filtered per user.

revoke all on public.sync_runs from anon, authenticated;

-- ---------------------------------------------------------------------------
-- profiles
-- ---------------------------------------------------------------------------
-- Own profile only. There is no cross-user profile lookup in Sunder — segments
-- and leaderboards are single-user in the MVP (spec 5.4).

create policy "profiles: read own"
  on public.profiles for select
  to authenticated
  using (auth.uid() = id);

create policy "profiles: update own"
  on public.profiles for update
  to authenticated
  using (auth.uid() = id)
  with check (auth.uid() = id);

-- No insert policy: the row is created by the on_auth_user_created trigger.
-- No delete policy: account deletion cascades from auth.users (spec section 9).

-- ---------------------------------------------------------------------------
-- activities
-- ---------------------------------------------------------------------------
-- Read and delete only. Activities are imported by the sync service, so there is
-- no legitimate client-side insert or update — and denying those means a leaked
-- anon key plus a stolen session still cannot fabricate or alter training data.
-- Delete stays available because removing an activity is a real UI action.

create policy "activities: read own"
  on public.activities for select
  to authenticated
  using (auth.uid() = user_id);

create policy "activities: delete own"
  on public.activities for delete
  to authenticated
  using (auth.uid() = user_id);

-- ---------------------------------------------------------------------------
-- segments
-- ---------------------------------------------------------------------------
-- Full CRUD: unlike activities, segments are authored by the user in the UI
-- (spec 5.4), so the client is their legitimate source.

create policy "segments: read own"
  on public.segments for select
  to authenticated
  using (auth.uid() = user_id);

-- WITH CHECK on insert is what stops a user from writing a row owned by someone
-- else; USING alone would only govern which rows they can see.
create policy "segments: insert own"
  on public.segments for insert
  to authenticated
  with check (auth.uid() = user_id);

create policy "segments: update own"
  on public.segments for update
  to authenticated
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

create policy "segments: delete own"
  on public.segments for delete
  to authenticated
  using (auth.uid() = user_id);

-- ---------------------------------------------------------------------------
-- segment_efforts
-- ---------------------------------------------------------------------------
-- Computed by segment matching, so read-only for the client.

create policy "segment_efforts: read own"
  on public.segment_efforts for select
  to authenticated
  using (auth.uid() = user_id);

-- ---------------------------------------------------------------------------
-- personal_records
-- ---------------------------------------------------------------------------
-- Computed by the sync service, so read-only for the client.

create policy "personal_records: read own"
  on public.personal_records for select
  to authenticated
  using (auth.uid() = user_id);
