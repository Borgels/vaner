import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 0.8.6 WS10 — Minimal vite + vitest config so the cockpit can be built
// in CI for the axe-core scan and so unit tests can run via `npm run
// test`. Earlier WSes (notably WS5 / WS6) may extend this with proxy
// config, alias paths, or coverage thresholds.

const daemonTarget = process.env.VITE_VANER_DAEMON_URL || 'http://127.0.0.1:8473'
const apiProxy = {
  target: daemonTarget,
  changeOrigin: true,
}

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/activity': apiProxy,
      '/artefacts': apiProxy,
      '/backend': apiProxy,
      '/compute': apiProxy,
      '/context': apiProxy,
      '/decisions': apiProxy,
      '/deep-run': apiProxy,
      '/events': apiProxy,
      '/external-state': apiProxy,
      '/focus': apiProxy,
      '/gateway': apiProxy,
      '/goals': apiProxy,
      '/hardware': apiProxy,
      '/heatmap': apiProxy,
      '/impact': apiProxy,
      '/jobs': apiProxy,
      '/learning': apiProxy,
      '/mcp': apiProxy,
      '/pinned-facts': apiProxy,
      '/plans': apiProxy,
      '/policy': apiProxy,
      '/predictions': apiProxy,
      '/prepared-work': apiProxy,
      '/resources': apiProxy,
      '/scenarios': apiProxy,
      '/setup': apiProxy,
      '/signals': apiProxy,
      '/skills': apiProxy,
      '/sources': apiProxy,
      '/status': apiProxy,
      '/work': apiProxy,
      '/work-products': apiProxy,
    },
  },
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
