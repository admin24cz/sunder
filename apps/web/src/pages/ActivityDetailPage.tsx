import { type ReactElement, lazy, Suspense } from 'react';
import { Link, useParams } from 'react-router-dom';

import { useActivity } from '@/features/activities/queries';
import {
  formatDateTime,
  formatDistance,
  formatDuration,
  formatElevation,
  formatHeartRate,
  formatPace,
} from '@/lib/format';

// MapLibre and its stylesheet are a large chunk. Loading them lazily keeps them
// off the activity list and the login page, which is what spec section 10's
// "maps lazy-loaded" and sub-2-second first render ask for.
const ActivityMap = lazy(async () => {
  const module = await import('@/components/ActivityMap');
  return { default: module.ActivityMap };
});

const TYPE_LABELS: Record<string, string> = {
  running: 'Běh',
  cycling: 'Kolo',
  swimming: 'Plavání',
  other: 'Jiná aktivita',
};

export function ActivityDetailPage(): ReactElement {
  const { id } = useParams<{ id: string }>();
  const { data: activity, isPending, isError, error } = useActivity(id ?? '');

  if (isPending) {
    return (
      <p role="status" aria-live="polite" className="text-slate-600 dark:text-slate-400">
        Načítám aktivitu…
      </p>
    );
  }

  if (isError) {
    return (
      <div role="alert">
        <p className="text-red-600 dark:text-red-400">{error.message}</p>
        <Link to="/" className="text-brand-600 dark:text-brand-500 mt-2 inline-block underline">
          Zpět na aktivity
        </Link>
      </div>
    );
  }

  const label = TYPE_LABELS[activity.type] ?? activity.type;

  return (
    <article>
      <Link to="/" className="text-sm text-slate-600 underline dark:text-slate-400">
        ← Zpět na aktivity
      </Link>

      <h1 className="mt-3 text-2xl font-bold tracking-tight">{label}</h1>
      <time dateTime={activity.started_at} className="text-slate-600 dark:text-slate-400">
        {formatDateTime(activity.started_at)}
      </time>

      <dl className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-3">
        <Metric label="Vzdálenost" value={formatDistance(activity.distance_meters)} />
        <Metric label="Čas" value={formatDuration(activity.duration_seconds)} />
        <Metric label="Tempo" value={formatPace(activity.avg_pace_seconds_per_km)} />
        <Metric label="Převýšení" value={formatElevation(activity.elevation_gain_meters)} />
        <Metric label="Průměrná TF" value={formatHeartRate(activity.avg_heart_rate)} />
        <Metric label="Maximální TF" value={formatHeartRate(activity.max_heart_rate)} />
      </dl>

      <section className="mt-8">
        <h2 className="text-lg font-semibold">Trasa</h2>
        {activity.track_geojson === null ? (
          // Not an error: an indoor run or a pool swim genuinely has no GPS.
          <p className="mt-2 text-slate-600 dark:text-slate-400">Tahle aktivita nemá GPS záznam.</p>
        ) : (
          <Suspense
            fallback={
              <div
                role="status"
                aria-live="polite"
                className="mt-2 flex h-80 items-center justify-center rounded-lg bg-slate-100 text-slate-500 dark:bg-slate-900"
              >
                Načítám mapu…
              </div>
            }
          >
            <div className="mt-2">
              <ActivityMap
                track={activity.track_geojson}
                label={`Trasa: ${label}, ${formatDateTime(activity.started_at)}`}
              />
            </div>
          </Suspense>
        )}
      </section>
    </article>
  );
}

function Metric({ label, value }: { label: string; value: string }): ReactElement {
  return (
    <div className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-1 text-lg font-semibold tabular-nums">{value}</dd>
    </div>
  );
}
