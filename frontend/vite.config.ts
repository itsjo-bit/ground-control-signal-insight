import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Forward all /api/* requests to the FastAPI backend.
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    exclude: [
      // Legacy pre-Vitest test files: use self-executing patterns, no describe/it blocks.
      // They validate logic via TypeScript only and are not wired to Vitest.
      'src/components/__tests__/MissionDecisionPanel.test.ts',
      'src/components/__tests__/MissionReportPanel.test.ts',
      'src/components/__tests__/OrbitBackground.test.ts',
      'src/components/__tests__/TransmissionNarrativePanel.test.ts',
      'src/components/__tests__/TransmissionOutcomeBanner.test.ts',
    ],
  },
})
