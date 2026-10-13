import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

const apiRoutes = [
  '/health', '/jobs', '/runs', '/strategies', '/datasets', '/compare',
  '/backtests', '/optimisations', '/walk-forwards', '/monte-carlo', '/robustness',
]

export default defineConfig({
  base: '/ui/',
  plugins: [react()],
  server: {
    proxy: Object.fromEntries(apiRoutes.map(path => [path, 'http://127.0.0.1:8765'])),
  },
  test: { environment: 'node' },
})

