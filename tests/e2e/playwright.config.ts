import { defineConfig } from "@playwright/test";

import { ADMIN_STORAGE_STATE, BASE_URL } from "./helpers";

export default defineConfig({
  testDir: ".",
  // A full page.reload() re-initializes the whole Open WebUI SPA (re-fetch
  // session user + model lists) before route content renders; on a cold stack
  // with unconfigured/slow provider discovery this can take well over 20s, so
  // give assertions and tests headroom.
  timeout: 180_000,
  expect: { timeout: 40_000 },
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
  },
});
