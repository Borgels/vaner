import { resolve } from 'node:path'

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

const outputDir = resolve(__dirname, '../../src/vaner/daemon/cockpit_assets/dist')

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: outputDir,
    emptyOutDir: true,
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/health': 'http://127.0.0.1:8473',
      '/status': 'http://127.0.0.1:8473',
      '/compute': 'http://127.0.0.1:8473',
      '/compute/devices': 'http://127.0.0.1:8473',
      '/scenarios': 'http://127.0.0.1:8473',
      '/pinned-facts': 'http://127.0.0.1:8473',
      '/skills': 'http://127.0.0.1:8473',
      '/events/stream': 'http://127.0.0.1:8473',
      '/cockpit/bootstrap.json': 'http://127.0.0.1:8473',
      '/impact': 'http://127.0.0.1:8473',
      '/gateway': 'http://127.0.0.1:8473',
      '/decisions/stream': 'http://127.0.0.1:8473',
      '/decisions': 'http://127.0.0.1:8473',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    coverage: {
      provider: 'v8',
    },
  },
})
