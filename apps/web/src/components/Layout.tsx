import type { ReactElement } from 'react';
import { NavLink, Outlet } from 'react-router-dom';

import { useAuth } from '@/features/auth/useAuth';

const NAV_LINKS = [
  { to: '/', label: 'Aktivity', end: true },
  { to: '/nastaveni', label: 'Nastavení', end: false },
] as const;

/** Shell shared by every signed-in page. */
export function Layout(): ReactElement {
  const { signOut } = useAuth();

  return (
    <div className="min-h-dvh">
      {/* Lets a keyboard user reach the content without tabbing the whole nav. */}
      <a
        href="#obsah"
        className="focus:bg-brand-600 sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:px-3 focus:py-2 focus:text-white"
      >
        Přeskočit na obsah
      </a>

      <header className="border-b border-slate-200 dark:border-slate-800">
        <div className="mx-auto flex max-w-3xl items-center gap-4 p-4">
          <span className="text-lg font-bold tracking-tight">Sunder</span>

          <nav aria-label="Hlavní" className="flex gap-3">
            {NAV_LINKS.map(({ to, label, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  isActive
                    ? 'text-brand-600 dark:text-brand-500 font-medium'
                    : 'text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100'
                }
              >
                {label}
              </NavLink>
            ))}
          </nav>

          <button
            type="button"
            onClick={() => void signOut()}
            className="ml-auto text-sm text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100"
          >
            Odhlásit
          </button>
        </div>
      </header>

      <main id="obsah" className="mx-auto max-w-3xl p-4">
        <Outlet />
      </main>
    </div>
  );
}
