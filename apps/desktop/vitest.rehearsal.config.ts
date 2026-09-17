import { defineConfig } from 'vitest/config'

// Phase 14 rehearsal only (npm run rehearsal:phase14). Never part of npm test.
export default defineConfig({
  test: {
    environment: 'node',
    include: ['tests/rehearsal/**/*.rehearsal.ts'],
    testTimeout: 1_800_000
  }
})
