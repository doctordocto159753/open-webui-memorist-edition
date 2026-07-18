import { expect, type APIRequestContext, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// The repo is an ES module ("type": "module"), so the CommonJS __dirname global
// is not defined; derive it from import.meta.url instead.
const moduleDir = path.dirname(fileURLToPath(import.meta.url));

export const STATE_PATH = path.join(moduleDir, ".state", "e2e-state.json");
export const STUB_URL = process.env.MEMORIST_E2E_STUB_URL || "http://localhost:9800";

export type E2EState = {
  admin: { name: string; email: string; password: string };
  member: { name: string; email: string; password: string };
  captureChatId?: string;
  retrievalChatId?: string;
  offChatId?: string;
  profileUuid?: string;
  stubRequestCountAfterOff?: number;
};

export function defaultState(): E2EState {
  return {
    admin: { name: "Admin", email: "admin@memorist.local", password: "memorist-admin-pass-1" },
    member: { name: "Member", email: "member@memorist.local", password: "memorist-member-pass-1" },
  };
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

export async function signUp(
  page: Page,
  user: { name: string; email: string; password: string },
): Promise<void> {
  await page.goto("/auth");
  const nameField = page.locator("#name");
  if (!(await nameField.isVisible().catch(() => false))) {
    // The form may default to sign-in; switch to sign-up when offered.
    const switcher = page.getByRole("button", { name: /sign up/i }).first();
    if (await switcher.isVisible().catch(() => false)) await switcher.click();
  }
  if (await nameField.isVisible().catch(() => false)) {
    await nameField.fill(user.name);
  }
  await page.locator("#email").fill(user.email);
  await page.locator("#password").fill(user.password);
  await page.locator('button[type="submit"]').first().click();
  await page.waitForURL((url) => !url.pathname.startsWith("/auth"), { timeout: 30_000 });
  await dismissOverlays(page);
}

export async function signIn(
  page: Page,
  user: { email: string; password: string },
): Promise<void> {
  await page.goto("/auth");
  const nameField = page.locator("#name");
  if (await nameField.isVisible().catch(() => false)) {
    const switcher = page.getByRole("button", { name: /sign in/i }).first();
    if (await switcher.isVisible().catch(() => false)) await switcher.click();
  }
  await page.locator("#email").fill(user.email);
  await page.locator("#password").fill(user.password);
  await page.locator('button[type="submit"]').first().click();
  await page.waitForURL((url) => !url.pathname.startsWith("/auth"), { timeout: 30_000 });
  await dismissOverlays(page);
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
