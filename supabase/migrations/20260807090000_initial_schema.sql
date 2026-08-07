-- Sunder — initial schema (spec section 8.1).
--
-- WHY this migration creates the tables *and* enables RLS, but defines no
-- policies: enabling RLS with zero policies is deny-all. Between this migration
-- and the next one every table is therefore completely unreachable through the
-- public anon key. That ordering guarantees there is no window — not even a
-- single statement wide — in which a table exists but is readable by anyone.
-- Policies are added deliberately, one at a time, in 20260807090100.

-- PostGIS lives in the `extensions` schema on Supabase. Pin the search path so
-- the geography type and its GiST operator classes resolve regardless of the
-- role's default search_path.
create extension if not exists postgis with schema extensions;
set search_path = public, extensions;

-- ---------------------------------------------------------------------------
-- Shared helpers
-- ---------------------------------------------------------------------------

-- WHY `security definer` + empty search_path: this runs with elevated rights on
-- every row update, so an attacker-controlled search_path must not be able to
-- shadow any function it calls. Empty search_path forces fully-qualified names.
create or replace function public.set_updated_at()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- profiles
-- ---------------------------------------------------------------------------
-- Application-level user data. `auth.users` stays owned by Supabase Auth; this
-- table is where anything Sunder-specific about a person belongs.

create table public.profiles (
  id          uuid primary key references auth.users (id) on delete cascade,
  display_name text,
  created_at  timestamptz not null default now()
);

comment on table public.profiles is
  'Sunder-specific user data. Row is created automatically on signup.';

-- WHY a trigger rather than a frontend insert: the profile must exist before the
-- user's first request, and making the client responsible for it means a failed
-- insert leaves an account in a half-created state.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id, display_name)
  values (new.id, new.raw_user_meta_data ->> 'display_name');
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ---------------------------------------------------------------------------
-- garmin_connections
-- ---------------------------------------------------------------------------
-- The most sensitive table in the system: it holds third-party credentials.
-- The password is stored only as an AES-256-GCM ciphertext produced by the sync
-- service; the key never enters this database (spec section 6.1).

create table public.garmin_connections (
  user_id                   uuid primary key references auth.users (id) on delete cascade,
  garmin_email              text not null,
  garmin_password_encrypted bytea not null,
  status                    text not null default 'active',
  last_sync_at              timestamptz,
  last_error                text,
  created_at                timestamptz not null default now(),
  updated_at                timestamptz not null default now(),

  -- Spec section 7.3. A CHECK rather than an enum type: statuses are an
  -- operational concern that will gain members, and altering a CHECK is a
  -- cheaper migration than altering an enum used by a column default.
  constraint garmin_connections_status_check
    check (status in ('active', 'auth_failed', 'rate_limited', 'disabled'))
);

comment on table public.garmin_connections is
  'Garmin credentials, encrypted. Unreachable via anon key by design — no RLS '
  'policies exist and all grants are revoked. Service role access only.';
comment on column public.garmin_connections.garmin_password_encrypted is
  'AES-256-GCM payload: version byte || 12-byte nonce || ciphertext+tag. '
  'The key lives only in GitHub Secrets, never here.';

create trigger garmin_connections_set_updated_at
  before update on public.garmin_connections
  for each row execute function public.set_updated_at();

-- The sync run selects the users it should process; spec 7.3 says everything
-- except `active` and `rate_limited` is skipped.
create index garmin_connections_status_idx
  on public.garmin_connections (status);

-- ---------------------------------------------------------------------------
-- activities
-- ---------------------------------------------------------------------------

create table public.activities (
  id                      uuid primary key default gen_random_uuid(),
  user_id                 uuid not null references auth.users (id) on delete cascade,

  -- Dedup key. Spec 5.2: re-running the sync must never create duplicates.
  garmin_activity_id      bigint not null,

  type                    text not null,
  started_at              timestamptz not null,
  duration_seconds        integer,
  distance_meters         numeric,
  elevation_gain_meters   numeric,
  avg_heart_rate          integer,
  max_heart_rate          integer,
  avg_pace_seconds_per_km numeric,

  -- WHY only a simplified track here: raw GPS blows past the 500 MB free tier
  -- fast (spec 8.2). This is Douglas-Peucker at ~10 m, which is enough for the
  -- map and for segment matching.
  track                   extensions.geography(LineString, 4326),

  -- Per-second heart rate / cadence / pace lives in Supabase Storage and is
  -- loaded lazily on the activity detail page. This is just the object path.
  stream_path             text,

  created_at              timestamptz not null default now(),

  -- Idempotence is enforced by the database, not by the sync service's
  -- bookkeeping: a bug or a concurrent run still cannot produce a duplicate.
  constraint activities_user_garmin_activity_key unique (user_id, garmin_activity_id)
);

comment on table public.activities is
  'One imported Garmin activity. Simplified track only; detailed streams live '
  'in Storage under stream_path.';

-- Spec 8.1 "indexes are required, not optional".
-- Main activity feed, newest first.
create index activities_user_started_idx
  on public.activities (user_id, started_at desc);
-- Filtered aggregations (per sport, per period).
create index activities_user_type_started_idx
  on public.activities (user_id, type, started_at desc);
-- Spatial queries for segment matching.
create index activities_track_idx
  on public.activities using gist (track);

-- ---------------------------------------------------------------------------
-- segments
-- ---------------------------------------------------------------------------

create table public.segments (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references auth.users (id) on delete cascade,
  name            text not null,
  geometry        extensions.geography(LineString, 4326) not null,
  distance_meters numeric,
  created_at      timestamptz not null default now()
);

comment on table public.segments is
  'User-defined stretch of route. MVP: drawn by hand from an existing activity.';

create index segments_user_idx
  on public.segments (user_id, created_at desc);
create index segments_geometry_idx
  on public.segments using gist (geometry);

-- ---------------------------------------------------------------------------
-- segment_efforts
-- ---------------------------------------------------------------------------

create table public.segment_efforts (
  id              uuid primary key default gen_random_uuid(),
  segment_id      uuid not null references public.segments (id) on delete cascade,
  activity_id     uuid not null references public.activities (id) on delete cascade,

  -- Denormalised from the parent rows. WHY: every RLS policy on this table
  -- filters by owner, and a policy that had to join through segments/activities
  -- would run on every single row read.
  user_id         uuid not null references auth.users (id) on delete cascade,

  elapsed_seconds integer not null,
  started_at      timestamptz,

  -- One effort per (segment, activity) — re-running detection updates rather
  -- than appends.
  constraint segment_efforts_segment_activity_key unique (segment_id, activity_id)
);

comment on table public.segment_efforts is
  'A detected pass through a segment during one activity.';

-- Leaderboard for a segment: fastest first.
create index segment_efforts_segment_elapsed_idx
  on public.segment_efforts (segment_id, elapsed_seconds);
create index segment_efforts_activity_idx
  on public.segment_efforts (activity_id);

-- ---------------------------------------------------------------------------
-- personal_records
-- ---------------------------------------------------------------------------

create table public.personal_records (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users (id) on delete cascade,

  -- 1k | 5k | 10k | half_marathon | marathon | longest | most_elevation | ...
  category    text not null,

  value       numeric not null,
  activity_id uuid references public.activities (id) on delete set null,
  achieved_at timestamptz,

  -- Exactly one current record per category, so recomputation is an upsert.
  constraint personal_records_user_category_key unique (user_id, category)
);

comment on table public.personal_records is
  'Current best per category. Recomputed by the sync service; one row per '
  '(user, category) so recomputation is an idempotent upsert.';

-- ---------------------------------------------------------------------------
-- sync_runs
-- ---------------------------------------------------------------------------
-- Spec 7.4: makes a failing sync diagnosable without digging through GitHub
-- Actions logs.

create table public.sync_runs (
  id                   uuid primary key default gen_random_uuid(),
  started_at           timestamptz not null default now(),
  finished_at          timestamptz,
  users_processed      integer not null default 0,
  activities_imported  integer not null default 0,

  -- Per-user failures, so one broken account does not hide the rest (spec 7.1).
  -- Shape: [{"user_id": "...", "error": "...", "type": "..."}]
  errors               jsonb not null default '[]'::jsonb
);

comment on table public.sync_runs is
  'Operational log of sync executions. Not user data — service role only.';

create index sync_runs_started_idx
  on public.sync_runs (started_at desc);

-- ---------------------------------------------------------------------------
-- Lock everything down before any policy exists
-- ---------------------------------------------------------------------------
-- RLS on + no policies = nothing is readable or writable through the anon or
-- authenticated roles. The service role bypasses RLS and keeps working.

alter table public.profiles           enable row level security;
alter table public.garmin_connections enable row level security;
alter table public.activities         enable row level security;
alter table public.segments           enable row level security;
alter table public.segment_efforts    enable row level security;
alter table public.personal_records   enable row level security;
alter table public.sync_runs          enable row level security;
