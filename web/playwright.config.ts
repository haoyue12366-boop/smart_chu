import { defineConfig } from '@playwright/test';
const serviceUrl = `http://127.0.0.1:${process.env.SMART_COOKING_E2E_PORT ?? '8000'}`;
process.env.SMART_COOKING_E2E_CLOCK ??= '1';
const reportRoot =
  process.env.SMART_COOKING_BROWSER_REPORT_ROOT ??
  `../data/verification/P5-browser/run-${new Date().toISOString().replace(/[:.]/g, '-')}`;
process.env.SMART_COOKING_BROWSER_REPORT_ROOT = reportRoot;
export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: serviceUrl,
    headless: true,
    viewport: { width: 1440, height: 1000 },
    ...(process.platform === 'win32' ? { channel: 'msedge' } : {}),
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  reporter: [
    ['list'],
    ['json', { outputFile: `${reportRoot}/results.json` }],
    ['junit', { outputFile: `${reportRoot}/results.xml` }],
    ['html', { outputFolder: `${reportRoot}/html`, open: 'never' }],
  ],
  outputDir: `${reportRoot}/artifacts`,
  webServer: {
    command: 'node ../scripts/start_p5_service.mjs',
    url: `${serviceUrl}/health/ready`,
    reuseExistingServer: false,
    timeout: 90_000,
  },
});
