/**
 * Validated access to the build-time environment.
 *
 * Vite substitutes `import.meta.env.VITE_*` at build time, so a missing
 * variable becomes the string `undefined` inside the bundle rather than a
 * runtime error. Without a check the app would boot, render, and then fail on
 * the first Supabase call with an opaque network error. Reading the environment
 * through this module turns that into one clear message at startup.
 */

interface AppEnv {
  readonly supabaseUrl: string;
  readonly supabaseAnonKey: string;
}

/**
 * The public variables this app is allowed to read.
 *
 * Spelled out rather than derived from `keyof ImportMetaEnv`, because Vite's
 * declaration carries a `[key: string]` index signature — deriving from it
 * would accept any name at all, including a secret one.
 */
type PublicEnvVar = 'VITE_SUPABASE_URL' | 'VITE_SUPABASE_ANON_KEY';

function required(name: PublicEnvVar, value: string | undefined): string {
  if (value === undefined || value === '' || value === 'undefined') {
    throw new Error(
      `Chybí proměnná prostředí ${name}. Zkopíruj .env.example do .env a doplň hodnoty ` +
        `(viz docs/setup.md).`,
    );
  }
  return value;
}

export const env: AppEnv = {
  supabaseUrl: required('VITE_SUPABASE_URL', import.meta.env.VITE_SUPABASE_URL),
  supabaseAnonKey: required('VITE_SUPABASE_ANON_KEY', import.meta.env.VITE_SUPABASE_ANON_KEY),
};
