import { defineConfig } from '@playwright/test';

// Point the suite at a deployment instead of the local server:
//   JUNGLE_BASE_URL=https://claude-jungle.pages.dev npm run test:e2e
// Same tests, real CDN, real headers — which is the only way to find out that a
// deploy actually serves a working engine rather than a working directory.
const BASE_URL = process.env.JUNGLE_BASE_URL || 'http://localhost:8788';
const isRemote = !BASE_URL.includes('localhost');

export default defineConfig({
  testDir: 'tests/e2e',
  timeout: 180_000,
  retries: 1,
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
  },
  ...(isRemote
    ? {}
    : {
        webServer: {
          command: 'node tools/serve.mjs 8788',
          url: 'http://localhost:8788',
          reuseExistingServer: true,
        },
      }),
});
