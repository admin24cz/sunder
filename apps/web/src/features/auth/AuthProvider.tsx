import type { Session } from '@supabase/supabase-js';
import {
  type ReactElement,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from 'react';

import { AuthContext, type AuthState } from '@/features/auth/AuthContext';
import { supabase } from '@/lib/supabase';

interface Props {
  children: ReactNode;
}

/**
 * Tracks the Supabase session and exposes it to the app.
 *
 * Two sources of truth have to be reconciled at startup: the session Supabase
 * restores from storage, and the stream of later auth events. Both are wired up
 * here so no component ever has to.
 */
export function AuthProvider({ children }: Props): ReactElement {
  const [session, setSession] = useState<Session | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let active = true;

    // Restore an existing session first, so a returning user is not shown the
    // login page for a frame before their session loads.
    void supabase.auth.getSession().then(({ data }) => {
      if (!active) return;
      setSession(data.session);
      setIsLoading(false);
    });

    // Then follow every later change: sign-in, sign-out, and the token refresh
    // that happens roughly hourly. Without this the app would keep using a
    // stale token and start failing reads once it expired.
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
      setIsLoading(false);
    });

    return () => {
      // `active` guards the promise above, which cannot be cancelled and would
      // otherwise set state after unmount under React's strict-mode double run.
      active = false;
      subscription.unsubscribe();
    };
  }, []);

  const signIn = useCallback(async (email: string, password: string): Promise<void> => {
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) {
      // Deliberately one message for both a wrong password and an unknown
      // address: distinguishing them would let anyone test whether a given
      // person has an account here.
      throw new Error('Nesprávný e-mail nebo heslo.');
    }
  }, []);

  const signOut = useCallback(async (): Promise<void> => {
    await supabase.auth.signOut();
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      session,
      user: session?.user ?? null,
      isLoading,
      signIn,
      signOut,
    }),
    [session, isLoading, signIn, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
