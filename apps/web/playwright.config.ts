import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30 * 60 * 1000,
  expect: { timeout: 2 * 60 * 1000 },
  fullyParallel: false,
  retries: 0,
  outputDir: process.env.CAMCAT_E2E_OUTPUT_DIR ?? "/data/zjr/camcat_studio/checks/browser/results",
  reporter: [["list"], ["html", {
    open: "never",
    outputFolder: process.env.CAMCAT_E2E_REPORT_DIR ?? "/data/zjr/camcat_studio/checks/browser/report",
  }]],
  use: {
    baseURL: process.env.CAMCAT_E2E_BASE_URL ?? "http://localhost:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
});

