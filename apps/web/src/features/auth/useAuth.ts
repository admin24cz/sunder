import { useContext } from 'react';

import { AuthContext, type AuthState } from '@/features/auth/AuthContext';

/**
 * Read the current auth state.
 *
 * @throws If used outside `AuthProvider`. Throwing rather than returning a null
 * default means the mistake surfaces immediately, instead of as a component
 * that quietly believes nobody is signed in.
 */
export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (context === null) {
    throw new Error('useAuth musí být použit uvnitř <AuthProvider>');
  }
  return context;
}
