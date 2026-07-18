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

export async function dismissOverlays(page: Page): Promise<void> {
  for (const label of ["Okay, Let's Go!", "Okay, Let’s Go!", "Close", "OK"]) {
    const button = page.getByRole("button", { name: label }).first();
    if (await button.isVisible().catch(() => false)) {
      await button.click().catch(() => undefined);
    }
  }
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
  await page.reload();
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
