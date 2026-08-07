import type { ReactElement } from 'react';

/**
 * Landing page.
 *
 * Placeholder until authentication and the activity list land — see spec
 * section 13, Phase 1.
 */
export function HomePage(): ReactElement {
  return (
    <main className="mx-auto max-w-2xl p-6">
      <h1 className="text-3xl font-bold tracking-tight">Sunder</h1>
      <p className="mt-2 text-slate-600 dark:text-slate-400">
        Tréninkový deník se synchronizací z Garmin Connect.
      </p>
    </main>
  );
}
