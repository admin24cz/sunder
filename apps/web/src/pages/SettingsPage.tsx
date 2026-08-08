import type { ReactElement } from 'react';

import { LinkGarminForm } from '@/features/garmin/LinkGarminForm';
import { useAuth } from '@/features/auth/useAuth';

export function SettingsPage(): ReactElement {
  const { user } = useAuth();

  return (
    <div className="space-y-10">
      <section>
        <h1 className="text-2xl font-bold tracking-tight">Nastavení</h1>
        <p className="mt-1 text-slate-600 dark:text-slate-400">
          Přihlášen jako {user?.email ?? '—'}
        </p>
      </section>

      <section>
        <h2 className="text-lg font-semibold">Garmin Connect</h2>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          Propojením povolíš Sunderu stahovat tvoje aktivity.
        </p>
        <div className="mt-4">
          <LinkGarminForm />
        </div>
      </section>
    </div>
  );
}
