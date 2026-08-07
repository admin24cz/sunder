import '@testing-library/jest-dom/vitest';

import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

// Unmount between tests so a leaked component cannot make the next test pass.
afterEach(() => {
  cleanup();
});

// The real client would try to reach Supabase during a component test. Stubbing
// the environment keeps `src/lib/env.ts` satisfied without a .env file, and the
// values are obvious fakes so nobody mistakes them for real credentials.
vi.stubEnv('VITE_SUPABASE_URL', 'http://localhost:54321');
vi.stubEnv('VITE_SUPABASE_ANON_KEY', 'test-anon-key');
