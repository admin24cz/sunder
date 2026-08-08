import type { ReactElement, ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';

import { useAuth } from '@/features/auth/useAuth';

interface Props {
  children: ReactNode;
}

/**
 * Redirects to the login page when nobody is signed in.
 *
 * Convenience, not security. Every table is protected by RLS in the database
 * (spec 6.2), so bypassing this in a browser console reveals nothing — it just
 * shows empty pages. The guard exists so a signed-out visitor gets a login form
 * rather than a working-looking app full of nothing.
 */
export function ProtectedRoute({ children }: Props): ReactElement {
  const { session, isLoading } = useAuth();
  const location = useLocation();

  // Waiting for the session to be restored is not the same as being signed out.
  // Redirecting here would bounce every returning user through the login page.
  if (isLoading) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex min-h-dvh items-center justify-center text-slate-600 dark:text-slate-400"
      >
        Načítám…
      </div>
    );
  }

  if (session === null) {
    // `state` carries where they were going, so signing in returns them there
    // rather than dumping them on the home page.
    return <Navigate to="/prihlaseni" replace state={{ from: location.pathname }} />;
  }

  return <>{children}</>;
}
