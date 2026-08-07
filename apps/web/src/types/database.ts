/**
 * Types for the Supabase schema.
 *
 * Kept in sync with `supabase/migrations/` by hand for now; once the project
 * exists, regenerate with:
 *
 *     supabase gen types typescript --project-id <ref> > src/types/database.ts
 *
 * Note which tables are absent: `garmin_connections` and `sync_runs` have no
 * entry here because the anon key cannot reach them at all (spec 6.2). If a
 * generated version ever reintroduces them, that is a signal the RLS grants
 * changed — not something to paper over.
 */

export type ActivityType = 'running' | 'cycling' | 'swimming';

export type PersonalRecordCategory =
  '1k' | '5k' | '10k' | 'half_marathon' | 'marathon' | 'longest' | 'most_elevation';

export interface Profile {
  id: string;
  display_name: string | null;
  created_at: string;
}

export interface Activity {
  id: string;
  user_id: string;
  garmin_activity_id: number;
  type: ActivityType;
  started_at: string;
  duration_seconds: number | null;
  distance_meters: number | null;
  elevation_gain_meters: number | null;
  avg_heart_rate: number | null;
  max_heart_rate: number | null;
  avg_pace_seconds_per_km: number | null;
  /** GeoJSON LineString, or null for an indoor activity with no GPS trace. */
  track: GeoJSON.LineString | null;
  stream_path: string | null;
  created_at: string;
}

export interface Segment {
  id: string;
  user_id: string;
  name: string;
  geometry: GeoJSON.LineString;
  distance_meters: number | null;
  created_at: string;
}

export interface SegmentEffort {
  id: string;
  segment_id: string;
  activity_id: string;
  user_id: string;
  elapsed_seconds: number;
  started_at: string | null;
}

export interface PersonalRecord {
  id: string;
  user_id: string;
  category: PersonalRecordCategory;
  value: number;
  activity_id: string | null;
  achieved_at: string | null;
}

/** Shape supabase-js expects, so `createClient<Database>` types every query. */
export interface Database {
  public: {
    Tables: {
      profiles: {
        Row: Profile;
        Insert: never;
        Update: Partial<Pick<Profile, 'display_name'>>;
      };
      activities: {
        Row: Activity;
        // No insert or update policy exists for the client: activities are
        // written by the sync service with the service role.
        Insert: never;
        Update: never;
      };
      segments: {
        Row: Segment;
        Insert: Omit<Segment, 'id' | 'created_at'> & { id?: string };
        Update: Partial<Omit<Segment, 'id' | 'user_id' | 'created_at'>>;
      };
      segment_efforts: {
        Row: SegmentEffort;
        Insert: never;
        Update: never;
      };
      personal_records: {
        Row: PersonalRecord;
        Insert: never;
        Update: never;
      };
    };
    Views: Record<never, never>;
    Functions: Record<never, never>;
    Enums: {
      activity_type: ActivityType;
      personal_record_category: PersonalRecordCategory;
    };
    CompositeTypes: Record<never, never>;
  };
}
