/// <reference types="vite/client" />

/**
 * Only public values may appear here.
 *
 * Vite inlines every `VITE_*` variable into the shipped JavaScript, so adding
 * a secret to this interface would publish it to every visitor. The service
 * role key and the encryption key belong to GitHub Secrets and the sync
 * workflow alone (spec section 6.3); a test in
 * `services/sync/tests/security/` fails the build if either shows up here.
 */
interface ImportMetaEnv {
  /** Supabase project URL, e.g. https://abcdefgh.supabase.co */
  readonly VITE_SUPABASE_URL: string;
  /** Public anon key. Safe to ship — RLS is what protects the data. */
  readonly VITE_SUPABASE_ANON_KEY: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
