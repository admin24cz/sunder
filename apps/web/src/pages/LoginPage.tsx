import { type ReactElement, type SubmitEvent, useState } from 'react';
import { Navigate } from 'react-router-dom';

import { useAuth } from '@/features/auth/useAuth';

/**
 * Sign-in page.
 *
 * No sign-up form: registration is closed while the instance is single-user
 * (spec 6.6), and accounts are created through the Supabase dashboard. Offering
 * a form that always failed would be worse than not offering one.
 */
export function LoginPage(): ReactElement {
  const { signIn, session, isLoading } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (isLoading) return <FullPageMessage>Načítám…</FullPageMessage>;
  if (session !== null) return <Navigate to="/" replace />;

  async function handleSubmit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await signIn(email, password);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Přihlášení se nezdařilo.');
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center p-6">
      <h1 className="text-3xl font-bold tracking-tight">Sunder</h1>
      <p className="mt-1 text-slate-600 dark:text-slate-400">Tréninkový deník</p>

      <form onSubmit={(e) => void handleSubmit(e)} className="mt-8 space-y-4">
        <div>
          <label htmlFor="email" className="block text-sm font-medium">
            E-mail
          </label>
          <input
            id="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
            }}
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 dark:border-slate-700 dark:bg-slate-900"
          />
        </div>

        <div>
          <label htmlFor="password" className="block text-sm font-medium">
            Heslo
          </label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
            }}
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 dark:border-slate-700 dark:bg-slate-900"
          />
        </div>

        {error !== null && (
          // `role="alert"` so a screen reader announces the failure rather than
          // leaving the user wondering why nothing happened (spec section 10).
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={isSubmitting}
          className="bg-brand-600 hover:bg-brand-700 w-full rounded-md px-4 py-2 font-medium text-white disabled:opacity-60"
        >
          {isSubmitting ? 'Přihlašuji…' : 'Přihlásit se'}
        </button>
      </form>
    </main>
  );
}

function FullPageMessage({ children }: { children: string }): ReactElement {
  return (
    <main className="flex min-h-dvh items-center justify-center p-6 text-slate-600 dark:text-slate-400">
      {children}
    </main>
  );
}
