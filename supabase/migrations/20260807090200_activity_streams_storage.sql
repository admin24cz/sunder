-- Sunder — Storage bucket for detailed activity streams (spec section 8.2).
--
-- WHY streams are not columns: per-second heart rate, cadence and pace run to
-- hundreds of kB per activity and would exhaust the 500 MB free tier within a
-- few hundred activities. The database keeps a simplified track plus a pointer
-- (activities.stream_path); the payload itself is a compressed object here and
-- is fetched lazily, only when an activity detail page is opened.

-- Private bucket: objects are reachable only through an authenticated request
-- that satisfies the policies below, never by guessing a public URL.
insert into storage.buckets (id, name, public)
values ('activity-streams', 'activity-streams', false)
on conflict (id) do nothing;

-- Path convention: `<user_id>/<activity_id>.json.gz`
--
-- The owner is encoded in the first path segment, which is what the policies
-- match on. That keeps authorisation a string comparison instead of a join back
-- into `activities` on every object read.

create policy "activity streams: read own"
  on storage.objects for select
  to authenticated
  using (
    bucket_id = 'activity-streams'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );

create policy "activity streams: delete own"
  on storage.objects for delete
  to authenticated
  using (
    bucket_id = 'activity-streams'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );

-- No insert or update policy: streams are written exclusively by the sync
-- service using the service role, which bypasses RLS. A client has no reason to
-- upload training data, and denying it removes a whole class of forged-data and
-- storage-quota-abuse problems.
