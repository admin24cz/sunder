-- A view exposing activity tracks as GeoJSON.
--
-- WHY this exists: PostgREST serialises a `geography` column as hex-encoded
-- EWKB. That is compact and correct, but MapLibre cannot draw it, and decoding
-- EWKB in the browser would mean shipping a parser to every visitor to undo
-- something Postgres can do for free.
--
-- WHY `security_invoker = true`: a view normally runs with its *owner's*
-- rights, which would bypass the RLS policies on `activities` entirely and hand
-- every user everyone else's tracks. With security_invoker the view executes as
-- the querying user, so the existing policy applies unchanged and this file adds
-- no new access path to audit.

create view public.activities_geo
with (security_invoker = true)
as
select
  id,
  user_id,
  garmin_activity_id,
  type,
  started_at,
  duration_seconds,
  distance_meters,
  elevation_gain_meters,
  avg_heart_rate,
  max_heart_rate,
  avg_pace_seconds_per_km,
  stream_path,
  created_at,
  -- NULL stays NULL: an indoor activity has no track, and the frontend renders
  -- that as "no map" rather than as an empty one.
  case
    when track is null then null
    else st_asgeojson(track)::jsonb
  end as track_geojson
from public.activities;

comment on view public.activities_geo is
  'activities with the track as GeoJSON instead of EWKB, for the map. '
  'security_invoker so the underlying RLS policies still apply.';

-- The anon role is never granted anything in this schema; only signed-in users
-- read their own rows, and the policy on `activities` decides which those are.
grant select on public.activities_geo to authenticated;
