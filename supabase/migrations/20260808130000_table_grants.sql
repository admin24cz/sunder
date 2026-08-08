-- Table privileges (spec 6.2).
--
-- WHY this migration exists: the schema migration enabled RLS and wrote
-- policies, but granted nothing. Pushing it to a real project and running the
-- security tests showed every request failing with
-- `permission denied for table` — including requests made with the service
-- role key.
--
-- The reason is that Postgres checks two things independently, and both must
-- pass:
--
--   1. Does this role hold a GRANT on the table at all?
--   2. Does an RLS policy admit this particular row?
--
-- Supabase's default privileges did not cover tables created this way, so
-- answer 1 was "no" for everyone and the policies were never consulted. That is
-- a fail-closed outcome, so nothing was ever exposed — but it also means the
-- policies were untested until now, which is exactly what spec section 11.3
-- warns about: RLS being assumed rather than verified.
--
-- Granting explicitly here is better than relying on defaults regardless. The
-- privileges of the most sensitive table in the project should be legible in
-- the migration history, not inherited from a platform default that can change.
--
-- The grants are deliberately narrower than "ALL", and they mirror the policies
-- exactly. Both layers have to agree before anything is reachable.

-- ---------------------------------------------------------------------------
-- anon: nothing, anywhere
-- ---------------------------------------------------------------------------
-- Not an omission. Every policy targets `authenticated`, so an anonymous
-- visitor has no legitimate read in this schema. With no grant at all, such a
-- request is refused before RLS is even reached.

revoke all on all tables in schema public from anon;

-- ---------------------------------------------------------------------------
-- authenticated: exactly what the policies allow
-- ---------------------------------------------------------------------------

-- Own profile: read and update. Insert is the signup trigger's job; delete
-- cascades from auth.users.
grant select, update on public.profiles to authenticated;

-- Activities are imported by the sync service, so no insert or update here.
-- Delete stays available because removing an activity is a real UI action.
grant select, delete on public.activities to authenticated;

-- Segments are authored by the user in the UI, so full CRUD.
grant select, insert, update, delete on public.segments to authenticated;

-- Computed by the sync service; read-only for clients.
grant select on public.segment_efforts to authenticated;
grant select on public.personal_records to authenticated;

-- The map view. security_invoker means the query also needs SELECT on
-- `activities` above, and the policy there still decides which rows appear.
grant select on public.activities_geo to authenticated;

-- garmin_connections and sync_runs are deliberately absent. They have no policy
-- and now no grant either — two independent barriers, so exposing credentials
-- would take two separate mistakes rather than one.

-- ---------------------------------------------------------------------------
-- service_role: full access
-- ---------------------------------------------------------------------------
-- The sync service writes on behalf of users who are not present to
-- authenticate. It is the only component holding this key (spec 6.3), and it
-- bypasses RLS — but bypassing RLS is not the same as holding a grant, which is
-- what the failing tests demonstrated.

grant all on all tables in schema public to service_role;
grant all on all sequences in schema public to service_role;
