import type { Session, User } from '@supabase/supabase-js';
import { createContext } from 'react';

export interface AuthState {
  /** The current session, or null when signed out. */
  session: Session | null;
  /** Convenience accessor for `session.user`. */
  user: User | null;
  /**
   * True until the initial session lookup finishes.
   *
   * Distinct from "signed out". Supabase restores a session from storage
   * asynchronously, so treating the first render as signed out would bounce a
   * returning user to the login page before their session had loaded.
   */
  isLoading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}

/**
 * Auth state for the whole app.
 *
 * In its own module, with no component alongside it, so Fast Refresh can keep
 * component state across edits — a file exporting both a context and a
 * component loses that.
 */
export const AuthContext = createContext<AuthState | null>(null);
