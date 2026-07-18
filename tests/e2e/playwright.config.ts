import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  timeout: 120_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "playwright-report" }],
    ["json", { outputFile: "playwright-results.json" }],
  ],
  use: {
    baseURL: process.env.MEMORIST_E2E_BASE_URL || "http://localhost:3000",
    // Open WebUI locks body scroll on several screens; a taller viewport keeps
    // controls (auth submit, settings actions) within the visible area.
    viewport: { width: 1280, height: 1024 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
});
