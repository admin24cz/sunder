import { createClient } from '@supabase/supabase-js';

import { env } from '@/lib/env';
import type { Database } from '@/types/database';

/**
 * The single Supabase client for the whole app.
 *
 * Created with the public anon key. That key is safe to ship — it grants
 * nothing on its own, because every table is behind RLS scoped to
 * `auth.uid()` (spec 6.2). What protects the data is the policy set in
 * `supabase/migrations/`, not the secrecy of this key.
 *
 * One instance rather than a factory: supabase-js keeps the auth session and
 * its refresh timer on the client, and a second instance would race the first
 * one refreshing the same token.
 */
export const supabase = createClient<Database>(env.supabaseUrl, env.supabaseAnonKey, {
  auth: {
    persistSession: true,
    autoRefreshToken: true,
    // The app is a SPA served from GitHub Pages with no server-side callback
    // route, so the OAuth redirect arrives as a URL fragment that the client
    // has to consume itself.
    detectSessionInUrl: true,
  },
});
