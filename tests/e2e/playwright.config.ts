import { defineConfig } from "@playwright/test";

import { ADMIN_STORAGE_STATE, BASE_URL } from "./helpers";

const browserExecutablePath = process.env.MEMORIST_E2E_BROWSER_EXECUTABLE?.trim();

export default defineConfig({
  testDir: ".",
  timeout: 120_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  // Bootstrap the admin account and write the authenticated storage state
  // before any test runs.
  globalSetup: "./global-setup.ts",
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "playwright-report" }],
    ["json", { outputFile: "playwright-results.json" }],
  ],
  use: {
    baseURL: BASE_URL,
    // Every default page starts authenticated as the admin created in global
    // setup; the product tests assume an admin session without re-logging in.
    storageState: ADMIN_STORAGE_STATE,
    // Open WebUI locks body scroll on several screens; a taller viewport keeps
    // controls (settings actions, disclosures) within the visible area.
    viewport: { width: 1280, height: 1024 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    launchOptions: browserExecutablePath
      ? { executablePath: browserExecutablePath }
      : undefined,
  },
});
