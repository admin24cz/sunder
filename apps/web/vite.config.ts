import { fileURLToPath, URL } from 'node:url';

import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],

  // GitHub Pages serves a project site from /<repo>/, so the base path has to
  // match or every asset URL 404s. Driven by an environment variable rather
  // than hardcoded, so `npm run dev` and a user-site deploy both still work.
  base: process.env.VITE_BASE_PATH ?? '/',

  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },

  build: {
    // Source maps would republish readable source on a public Pages site. That
    // is not a secret leak — the repository is public anyway — but it doubles
    // the deployed payload for no benefit.
    sourcemap: false,
  },
});
