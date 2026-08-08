import { useQuery, type UseQueryResult } from '@tanstack/react-query';

import { supabase } from '@/lib/supabase';
import type { Activity, ActivityType } from '@/types/database';

/**
 * An activity as the map needs it: the track already decoded to GeoJSON.
 *
 * Read from the `activities_geo` view rather than the table, because PostgREST
 * serialises a PostGIS `geography` as hex EWKB, which MapLibre cannot draw.
 */
export interface ActivityWithTrack extends Omit<Activity, 'track'> {
  track_geojson: GeoJSON.LineString | null;
}

const VIEW = 'activities_geo';

/** Rows per page. Spec section 10 requires the activity list be paginated. */
export const PAGE_SIZE = 20;

export const activityKeys = {
  all: ['activities'] as const,
  list: (page: number, type: ActivityType | 'all') =>
    [...activityKeys.all, 'list', page, type] as const,
  detail: (id: string) => [...activityKeys.all, 'detail', id] as const,
};

interface ActivityPage {
  activities: ActivityWithTrack[];
  total: number;
  hasMore: boolean;
}

/**
 * Fetch one page of the user's activities, newest first.
 *
 * No `user_id` filter is applied, and that is not an oversight: RLS scopes
 * every read to `auth.uid()` in the database. Filtering here as well would
 * imply the client is what enforces it, which is exactly the misunderstanding
 * spec section 6.2 guards against.
 */
export function useActivities(
  page: number,
  type: ActivityType | 'all' = 'all',
): UseQueryResult<ActivityPage> {
  return useQuery({
    queryKey: activityKeys.list(page, type),
    queryFn: async (): Promise<ActivityPage> => {
      const from = page * PAGE_SIZE;
      const to = from + PAGE_SIZE - 1;

      let query = supabase
        .from(VIEW)
        .select('*', { count: 'exact' })
        .order('started_at', { ascending: false })
        .range(from, to);

      if (type !== 'all') {
        query = query.eq('type', type);
      }

      const { data, error, count } = await query;
      if (error) throw new Error(error.message);

      const activities = data as unknown as ActivityWithTrack[];
      const total = count ?? 0;
      return { activities, total, hasMore: to < total - 1 };
    },
    // Keeps the previous page on screen while the next loads, instead of
    // collapsing the list to a spinner on every page change.
    placeholderData: (previous) => previous,
  });
}

/** Fetch one activity, including its track. */
export function useActivity(id: string): UseQueryResult<ActivityWithTrack> {
  return useQuery({
    queryKey: activityKeys.detail(id),
    queryFn: async (): Promise<ActivityWithTrack> => {
      const { data, error } = await supabase.from(VIEW).select('*').eq('id', id).maybeSingle();

      if (error) throw new Error(error.message);
      // `maybeSingle` returns null both for a missing row and for one RLS hides.
      // The two are indistinguishable to the client on purpose — that is what
      // stops the app confirming that somebody else's activity exists.
      if (data === null) throw new Error('Aktivita nenalezena.');

      return data;
    },
  });
}
