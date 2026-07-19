/**
 * Playwright global setup for the PR5-G product E2E.
 *
 * The composed Open WebUI exposes only a sign-in form, so the first admin is
 * bootstrapped here through the signup API (the first account is always
 * allowed). The resulting token is written as a browser storage state so every
 * default page in the suite starts authenticated as that admin — the product
 * tests assume an authenticated admin session without re-logging in.
 */
import { request as apiRequest } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

import { ADMIN, ADMIN_STORAGE_STATE, BASE_URL, bootstrapAdmin } from "./helpers";

async function globalSetup(): Promise<void> {
  const context = await apiRequest.newContext({ baseURL: BASE_URL });
  try {
    const token = await bootstrapAdmin(context, ADMIN);
    const storageState = {
      cookies: [],
      origins: [{ origin: BASE_URL, localStorage: [{ name: "token", value: token }] }],
    };
    fs.mkdirSync(path.dirname(ADMIN_STORAGE_STATE), { recursive: true });
    fs.writeFileSync(ADMIN_STORAGE_STATE, JSON.stringify(storageState, null, 2));
  } finally {
    await context.dispose();
  }
}

export default globalSetup;
