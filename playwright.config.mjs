import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: 'tests/e2e',
  timeout: 180_000,
  retries: 1,
  use: {
    baseURL: 'http://localhost:8788',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'node tools/serve.mjs 8788',
    url: 'http://localhost:8788',
    reuseExistingServer: true,
  },
});
