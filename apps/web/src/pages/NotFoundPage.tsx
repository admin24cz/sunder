import type { ReactElement } from 'react';
import { Link } from 'react-router-dom';

export function NotFoundPage(): ReactElement {
  return (
    <main className="mx-auto max-w-md p-6 text-center">
      <h1 className="text-2xl font-bold tracking-tight">Stránka nenalezena</h1>
      <p className="mt-2 text-slate-600 dark:text-slate-400">Tahle adresa nikam nevede.</p>
      <Link to="/" className="text-brand-600 dark:text-brand-500 mt-4 inline-block underline">
        Zpět na aktivity
      </Link>
    </main>
  );
}
