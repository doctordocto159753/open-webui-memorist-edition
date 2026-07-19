import { expect, type APIRequestContext, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// The repo is an ES module ("type": "module"), so the CommonJS __dirname global
// is not defined; derive it from import.meta.url instead.
const moduleDir = path.dirname(fileURLToPath(import.meta.url));

export const STATE_PATH = path.join(moduleDir, ".state", "e2e-state.json");
export const STUB_URL = process.env.MEMORIST_E2E_STUB_URL || "http://localhost:9800";
export const BASE_URL = process.env.MEMORIST_E2E_BASE_URL || "http://localhost:3000";
// Persisted admin browser session produced by global setup and consumed via
// the Playwright `storageState` option so every default page is authenticated.
export const ADMIN_STORAGE_STATE = path.join(moduleDir, ".state", "admin-storage.json");

export type Credentials = { name: string; email: string; password: string };

export const ADMIN: Credentials = {
  name: "Admin",
  email: "admin@memorist.local",
  password: "memorist-admin-pass-1",
};
export const MEMBER: Credentials = {
  name: "Member",
  email: "member@memorist.local",
  password: "memorist-member-pass-1",
};

export type E2EState = {
  admin: Credentials;
  member: Credentials;
  captureChatId?: string;
  retrievalChatId?: string;
  offChatId?: string;
  profileUuid?: string;
  stubRequestCountAfterOff?: number;
};

export function defaultState(): E2EState {
  return { admin: { ...ADMIN }, member: { ...MEMBER } };
}

export function loadState(): E2EState {
  if (!fs.existsSync(STATE_PATH)) return defaultState();
  return { ...defaultState(), ...JSON.parse(fs.readFileSync(STATE_PATH, "utf-8")) };
}

export function saveState(state: E2EState): void {
  fs.mkdirSync(path.dirname(STATE_PATH), { recursive: true });
  fs.writeFileSync(STATE_PATH, JSON.stringify(state, null, 2));
}

async function dismissUpdateNotification(page: Page): Promise<void> {
  // Open WebUI's update notification uses an icon-only X button without an
  // accessible name. Locate it by its stable product copy, use the real close
  // control, and verify the pointer-blocking toast is gone before continuing.
  const updateToast = page
    .locator("div.absolute.bottom-8.right-8.z-50")
    .filter({ hasText: "A new version" });

  const updateExpected = await page.evaluate(async () => {
    const dismissedAt = Number(window.localStorage.getItem("dismissedUpdateToast") || "0");
    if (dismissedAt && Date.now() - dismissedAt <= 24 * 60 * 60 * 1000) return false;

    const token = window.localStorage.getItem("token");
    if (!token) return false;
    const userResponse = await fetch("/api/v1/auths/", {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!userResponse.ok) return false;
    const user = (await userResponse.json()) as { role?: string };
    if (user.role !== "admin") return false;

    const configResponse = await fetch("/api/config");
    if (!configResponse.ok) return false;
    const config = (await configResponse.json()) as {
      features?: { enable_version_update_check?: boolean };
    };
    if (!config.features?.enable_version_update_check) return false;

    const versionResponse = await fetch("/api/version/updates", {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!versionResponse.ok) return false;
    const version = (await versionResponse.json()) as { current?: string; latest?: string };
    return Boolean(version.current && version.latest && version.current !== version.latest);
  });

  if (updateExpected) {
    await expect(updateToast).toBeVisible();
  }
  if (updateExpected || (await updateToast.isVisible().catch(() => false))) {
    await updateToast.locator("button").click();
    await expect(updateToast).toBeHidden();
  }
}

export async function dismissOverlays(
  page: Page,
  options: { waitForChangelog?: boolean } = {},
): Promise<void> {
  const changelogDialog = page
    .getByRole("dialog")
    .filter({ hasText: "What's New in Open WebUI" });

  if (options.waitForChangelog) {
    const changelogExpected = await page.evaluate(async () => {
      const token = window.localStorage.getItem("token");
      if (!token) return false;

      const [configResponse, settingsResponse] = await Promise.all([
        fetch("/api/config"),
        fetch("/api/v1/users/user/settings", {
          headers: { Authorization: `Bearer ${token}` },
        }),
      ]);
      if (!configResponse.ok || !settingsResponse.ok) {
        throw new Error(
          `overlay readiness failed: config=${configResponse.status} settings=${settingsResponse.status}`,
        );
      }

      const config = (await configResponse.json()) as { version?: string };
      const userSettings = (await settingsResponse.json()) as {
        ui?: { version?: string; showChangelog?: boolean };
      } | null;
      return (
        (userSettings?.ui?.showChangelog ?? true) &&
        userSettings?.ui?.version !== config.version
      );
    });

    if (changelogExpected) {
      await expect(changelogDialog).toBeVisible({ timeout: 30_000 });
      await changelogDialog.getByRole("button", { name: "Okay, Let's Go!" }).click();
      await expect(changelogDialog).toBeHidden();
    }
  }

  for (const label of ["Okay, Let's Go!", "Okay, Let’s Go!", "Close", "OK"]) {
    const button = page.getByRole("button", { name: label }).first();
    if (await button.isVisible().catch(() => false)) {
      await button.click().catch(() => undefined);
    }
  }

  await dismissUpdateNotification(page);
  await page.keyboard.press("Escape").catch(() => undefined);
}

/**
 * Bootstrap the first admin against the composed Open WebUI. The first signup
 * is always allowed even when the UI only offers a sign-in form; a repeat call
 * (e.g. phase 2 after restart) falls back to sign-in. Returns the admin token.
 */
export async function bootstrapAdmin(
  request: APIRequestContext,
  user: Credentials = ADMIN,
): Promise<string> {
  let response = await request.post(`${BASE_URL}/api/v1/auths/signup`, {
    data: { name: user.name, email: user.email, password: user.password },
    failOnStatusCode: false,
  });
  if (!response.ok()) {
    response = await request.post(`${BASE_URL}/api/v1/auths/signin`, {
      data: { email: user.email, password: user.password },
    });
  }
  if (!response.ok()) {
    throw new Error(`admin bootstrap failed: ${response.status()} ${await response.text()}`);
  }
  return (await response.json()).token as string;
}

/**
 * Authenticate ``page`` as ``user`` through the API and inject the token into
 * the SPA's storage. The composed Open WebUI exposes only a sign-in form whose
 * submit button sits in a scroll-locked container that Playwright cannot click,
 * so token injection is the reliable, product-faithful path (the SPA reads the
 * same ``localStorage`` token for every authenticated request).
 */
export async function authenticate(page: Page, user: Credentials): Promise<string> {
  const response = await page.request.post(`${BASE_URL}/api/v1/auths/signin`, {
    data: { email: user.email, password: user.password },
    failOnStatusCode: true,
  });
  const token = (await response.json()).token as string;
  await page.goto("/");
  await page.evaluate((value) => window.localStorage.setItem("token", value), token);
  // Reload so the SPA boots with the token, and wait until it has fetched the
  // current user (which populates the admin session) before returning.
  // Otherwise a follow-up navigation to a gated admin route (e.g. the Memorist
  // settings tab) races the session bootstrap and the tab has not rendered yet.
  const sessionReady = page
    .waitForResponse(
      (r) => /\/api\/v1\/auths\/?(\?|$)/.test(r.url()) && r.request().method() === "GET",
      { timeout: 30_000 },
    )
    .catch(() => undefined);
  await page.reload();
  await sessionReady;
  await dismissOverlays(page);
  return token;
}

// The suite's sign-up/sign-in entry points are both authentication: the admin
// account itself is created once in global setup via bootstrapAdmin().
export async function signUp(page: Page, user: Credentials): Promise<void> {
  await authenticate(page, user);
}

export async function signIn(page: Page, user: Credentials): Promise<void> {
  await authenticate(page, user);
}

export async function sendChatMessage(page: Page, text: string): Promise<void> {
  const input = page.locator("#chat-input");
  await expect(input).toBeVisible({ timeout: 30_000 });
  await input.click();
  await input.fill(text);
  // The update check is asynchronous and can finish after route-level overlay
  // cleanup. Close its real control at the last safe point before Send.
  await dismissUpdateNotification(page);
  await page.locator("#send-message-button").click();
}

export async function waitForAssistantReply(page: Page, fragment: string | RegExp): Promise<void> {
  await expect(page.locator(".chat-assistant").last()).toContainText(fragment, {
    timeout: 60_000,
  });
}

export async function currentChatId(page: Page): Promise<string> {
  await page.waitForURL(/\/c\/[0-9a-f-]+/i, { timeout: 30_000 });
  const match = page.url().match(/\/c\/([0-9a-f-]+)/i);
  if (!match) throw new Error(`no chat id in url ${page.url()}`);
  return match[1];
}

export type StubRequest = {
  path: string;
  model: string | null;
  stream: boolean;
  authorization_header_present: boolean;
  message_count: number;
  messages: Array<{
    role: string | null;
    name: string | null;
    content_excerpt: string;
    has_memory_context_attachment: boolean;
  }>;
};

export async function stubRequests(request: APIRequestContext): Promise<StubRequest[]> {
  const response = await request.get(`${STUB_URL}/__requests`);
  expect(response.ok()).toBeTruthy();
  return (await response.json()).requests as StubRequest[];
}

/** The memory workflow toggle rendered near the composer (open shadow DOM). */
export function composerToggle(page: Page) {
  return page.locator('memorist-memory-workflow-toggle button[role="switch"]').first();
}

export async function apiFetch(
  page: Page,
  url: string,
  init?: { method?: string; body?: unknown },
): Promise<{ status: number; body: unknown }> {
  return page.evaluate(
    async ({ url, init }) => {
      const token = localStorage.getItem("token");
      const response = await fetch(url, {
        method: init?.method || "GET",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: init?.body === undefined ? undefined : JSON.stringify(init.body),
        credentials: "include",
      });
      let body: unknown = null;
      try {
        body = await response.json();
      } catch {
        body = await response.text().catch(() => null);
      }
      return { status: response.status, body };
    },
    { url, init },
  );
}
