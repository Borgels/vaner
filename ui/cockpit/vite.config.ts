import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 0.8.6 WS10 — Minimal vite + vitest config so the cockpit can be built
// in CI for the axe-core scan and so unit tests can run via `npm run
// test`. Earlier WSes (notably WS5 / WS6) may extend this with proxy
// config, alias paths, or coverage thresholds.

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    sourcemap: true,
    target: 'es2022',
  },
  test: {
    globals: false,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}', 'tests/**/*.spec.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov'],
    },
  },
})
