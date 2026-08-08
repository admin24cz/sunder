import { type ReactElement, useState } from 'react';
import { Link } from 'react-router-dom';

import { PAGE_SIZE, useActivities } from '@/features/activities/queries';
import { formatDateTime, formatDistance, formatDuration, formatPace } from '@/lib/format';
import type { ActivityType } from '@/types/database';

const TYPE_LABELS: Record<ActivityType | 'all', string> = {
  all: 'Vše',
  running: 'Běh',
  cycling: 'Kolo',
  swimming: 'Plavání',
};

/** Paginated list of the user's activities, newest first. */
export function ActivitiesPage(): ReactElement {
  const [page, setPage] = useState(0);
  const [type, setType] = useState<ActivityType | 'all'>('all');
  const { data, isPending, isError, error } = useActivities(page, type);

  function changeType(next: ActivityType | 'all'): void {
    setType(next);
    // Page 3 of runs is rarely page 3 of rides; keeping the offset would land
    // the user on an empty page for no reason.
    setPage(0);
  }

  return (
    <section>
      <h1 className="text-2xl font-bold tracking-tight">Aktivity</h1>

      <div role="group" aria-label="Filtr podle typu" className="mt-4 flex flex-wrap gap-2">
        {(Object.keys(TYPE_LABELS) as (ActivityType | 'all')[]).map((option) => (
          <button
            key={option}
            type="button"
            aria-pressed={type === option}
            onClick={() => {
              changeType(option);
            }}
            className={
              type === option
                ? 'bg-brand-600 rounded-full px-3 py-1 text-sm font-medium text-white'
                : 'rounded-full border border-slate-300 px-3 py-1 text-sm dark:border-slate-700'
            }
          >
            {TYPE_LABELS[option]}
          </button>
        ))}
      </div>

      {/* Spec section 10: every async operation has a loading, error and empty state. */}
      {isPending && (
        <p role="status" aria-live="polite" className="mt-6 text-slate-600 dark:text-slate-400">
          Načítám aktivity…
        </p>
      )}

      {isError && (
        <p role="alert" className="mt-6 text-red-600 dark:text-red-400">
          Aktivity se nepodařilo načíst: {error.message}
        </p>
      )}

      {data?.activities.length === 0 && (
        <div className="mt-6 rounded-lg border border-dashed border-slate-300 p-6 text-center dark:border-slate-700">
          <p className="font-medium">Zatím tu nic není</p>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
            Aktivity se objeví po první synchronizaci. Propoj si Garmin účet v{' '}
            <Link to="/nastaveni" className="text-brand-600 dark:text-brand-500 underline">
              nastavení
            </Link>
            .
          </p>
        </div>
      )}

      {data !== undefined && data.activities.length > 0 && (
        <>
          <ul className="mt-6 space-y-2">
            {data.activities.map((activity) => (
              <li key={activity.id}>
                <Link
                  to={`/aktivita/${activity.id}`}
                  className="block rounded-lg border border-slate-200 p-4 hover:border-slate-400 dark:border-slate-800 dark:hover:border-slate-600"
                >
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="font-medium">{TYPE_LABELS[activity.type]}</span>
                    <time dateTime={activity.started_at} className="text-sm text-slate-500">
                      {formatDateTime(activity.started_at)}
                    </time>
                  </div>
                  <dl className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-sm text-slate-600 dark:text-slate-400">
                    <div className="flex gap-1">
                      <dt>Vzdálenost:</dt>
                      <dd className="font-medium">{formatDistance(activity.distance_meters)}</dd>
                    </div>
                    <div className="flex gap-1">
                      <dt>Čas:</dt>
                      <dd className="font-medium">{formatDuration(activity.duration_seconds)}</dd>
                    </div>
                    <div className="flex gap-1">
                      <dt>Tempo:</dt>
                      <dd className="font-medium">
                        {formatPace(activity.avg_pace_seconds_per_km)}
                      </dd>
                    </div>
                  </dl>
                </Link>
              </li>
            ))}
          </ul>

          <nav aria-label="Stránkování" className="mt-6 flex items-center justify-between">
            <button
              type="button"
              disabled={page === 0}
              onClick={() => {
                setPage((p) => p - 1);
              }}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-40 dark:border-slate-700"
            >
              Předchozí
            </button>

            <span aria-live="polite" className="text-sm text-slate-600 dark:text-slate-400">
              {page * PAGE_SIZE + 1}–{page * PAGE_SIZE + data.activities.length} z {data.total}
            </span>

            <button
              type="button"
              disabled={!data.hasMore}
              onClick={() => {
                setPage((p) => p + 1);
              }}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-40 dark:border-slate-700"
            >
              Další
            </button>
          </nav>
        </>
      )}
    </section>
  );
}
