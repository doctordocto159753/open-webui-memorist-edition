/**
 * PR5-G phase 1 browser E2E against the actual composed release artifact.
 *
 * Order matters: this file runs serially against one live stack. Phase 2
 * (post-restart.spec.ts) runs after CI restarts the stack through the
 * packaged lifecycle scripts and consumes the state saved here.
 */
import { expect, test } from "@playwright/test";

import {
  apiFetch,
  composerToggle,
  currentChatId,
  defaultState,
  dismissOverlays,
  saveState,
  sendChatMessage,
  signUp,
  stubRequests,
  waitForAssistantReply,
} from "./helpers";

const state = defaultState();
const STUB_KEY_CANARY =
  process.env.MEMORIST_E2E_STUB_KEY ||
  "sk-" + "memorist-e2e-canary-000000000000";
const STUB_PROVIDER_URL =
  process.env.MEMORIST_E2E_PROVIDER_URL || "http://host.docker.internal:9800/v1";
const CAPTURE_FACT = "My dog is named Alpha and her preferred food is chicken.";
const RETRIEVAL_QUESTION = "What is my dog's name and preferred food?";
const OFF_FACT = "My secret turtle is named Umbra and prefers dragonfruit.";

test.describe.configure({ mode: "serial" });

const consoleLines: string[] = [];

test.beforeEach(({ page }) => {
  page.on("console", (message) => consoleLines.push(message.text()));
});

test("direct Memorist settings load does not wait for model discovery", async ({ page }) => {
  let releaseModels!: () => void;
  let modelsReleased = false;
  let modelsRequestHeld = false;
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  const modelsGate = new Promise<void>((resolve) => {
    releaseModels = () => {
      modelsReleased = true;
      resolve();
    };
  });

  await page.route("**/api/models*", async (route) => {
    modelsRequestHeld = true;
    await modelsGate;
    await route.abort("failed");
  });

  const setupResponse = page.waitForResponse(
    (response) =>
      response.url().includes("/api/v1/memorist/model-control/setup/status") &&
      response.status() === 200,
  );

  try {
    await page.goto("/settings/memorist/memory-setup", {
      waitUntil: "domcontentloaded",
    });

    await expect.poll(() => modelsRequestHeld, { timeout: 10_000 }).toBe(true);
    await expect(page.locator('[data-memorist-page="memory-setup"]')).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.locator("memorist-memory-node-setup h1")).toContainText(
      /Memory processing nodes/i,
      { timeout: 10_000 },
    );
    expect(modelsReleased).toBe(false);

    expect((await setupResponse).status()).toBe(200);
    await expect(page).not.toHaveURL(/\/auth(?:[/?]|$)/);
    await expect(page.locator("[data-memorist-unauthorized]")).toHaveCount(0);

    const modelFailure = page.waitForEvent(
      "requestfailed",
      (request) => request.url().includes("/api/models"),
    );
    releaseModels();
    await modelFailure;

    await expect(page.locator('[data-memorist-page="memory-setup"]')).toBeVisible();
    expect(pageErrors).toEqual([]);
    await expect(page.locator("[data-sonner-toast]")).toHaveCount(0);
  } finally {
    releaseModels();
  }
});

test("absent session redirects before privileged Memorist content mounts", async ({ browser }) => {
  const context = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const page = await context.newPage();
  let privilegedApiRequested = false;
  page.on("request", (request) => {
    if (request.url().includes("/api/v1/memorist/model-control/setup/status")) {
      privilegedApiRequested = true;
    }
  });

  try {
    await page.goto("/settings/memorist/memory-setup", {
      waitUntil: "domcontentloaded",
    });

    await expect(page).toHaveURL(/\/auth(?:[/?]|$)/);
    await expect(page.locator('[data-memorist-page="memory-setup"]')).toHaveCount(0);
    await expect(page.locator("memorist-memory-node-setup")).toHaveCount(0);
    expect(privilegedApiRequested).toBe(false);
  } finally {
    await context.close();
  }
});

test("non-Memorist routes retain provider-dependent initialization", async ({ page }) => {
  let releaseModels!: () => void;
  let modelsRequestHeld = false;
  const modelsGate = new Promise<void>((resolve) => {
    releaseModels = resolve;
  });

  await page.route("**/api/models*", async (route) => {
    modelsRequestHeld = true;
    await modelsGate;
    // Complete discovery deterministically after proving this route remains
    // blocked on it. Backend provider reachability is asserted separately by
    // the composed smoke gate and the processing-node provider test.
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: [
          {
            id: "memorist-e2e-stub",
            name: "memorist-e2e-stub",
            object: "model",
            owned_by: "memorist-e2e",
          },
        ],
      }),
    });
  });

  try {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await expect.poll(() => modelsRequestHeld, { timeout: 10_000 }).toBe(true);
    await expect(page.locator("#chat-input")).toHaveCount(0);

    releaseModels();
    await expect(page.locator("#chat-input")).toBeVisible();
    await dismissOverlays(page, { waitForChangelog: true });
    const modelSelector = page.locator("#model-selector-0-button");
    await expect(modelSelector).toBeVisible();
    await modelSelector.click();
    await expect(page.getByRole("option", { name: /memorist-e2e-stub/i })).toBeVisible();

    await page.goto("/admin/settings", { waitUntil: "domcontentloaded" });
    await expect(page.locator('a[href="/settings/memorist/memory-setup"]')).toBeVisible();
  } finally {
    releaseModels();
  }
});

test("first launch: admin signup and visible Memorist navigation", async ({ page }) => {
  await signUp(page, state.admin);

  await page.goto("/admin/settings", { waitUntil: "domcontentloaded" });
  await dismissOverlays(page, { waitForChangelog: true });
  const memoristNav = page.locator('a[href="/settings/memorist/memory-setup"]');
  await expect(memoristNav).toBeVisible();
  await expect(memoristNav).toContainText("Memorist");

  // Memory Setup loads through the authenticated proxy.
  const setupResponse = page.waitForResponse(
    (response) =>
      response.url().includes("/api/v1/memorist/model-control/setup/status") &&
      response.status() === 200,
  );
  await memoristNav.click();
  await setupResponse;
  await expect(page.locator('[data-memorist-page="memory-setup"]')).toBeVisible();
  await expect(page.locator('[data-memorist-nav="Memory Setup"]')).toBeVisible();
  // CSS locators pierce the component's open shadow root.
  await expect(page.locator("memorist-memory-node-setup h1")).toContainText(
    /Memory processing nodes/i,
  );

  // Direct reload and back/forward keep working (real routes, not a modal).
  await page.reload();
  await expect(page.locator("memorist-memory-node-setup h1")).toContainText(
    /Memory processing nodes/i,
  );

  await page.locator('[data-memorist-nav="Diagnostics"]').click();
  await expect(page.locator("memorist-diagnostics")).toContainText(/Diagnostics/i);
  await expect(page.locator("memorist-diagnostics")).toContainText(/Memorist Core/i);
  await page.goBack();
  await expect(page.locator("memorist-memory-node-setup")).toBeVisible();

  await page.goto("/settings/memorist/import");
  await expect(page.locator("memorist-import-workflow")).toBeVisible();

  saveState(state);
});

test("processing nodes: create, acknowledge, test, and assign a profile", async ({ page }) => {
  await page.goto("/settings/memorist/processing-nodes");
  const nodes = page.locator("memorist-processing-nodes-settings");
  await expect(nodes).toContainText(/Processing Nodes/i);

  await nodes.locator('[data-action="new"]').click();
  const form = nodes.locator('form[data-action="save-profile"]');
  await form.locator('[name="profile_name"]').fill("E2E extraction stub");
  await form.locator('[name="role"]').selectOption("memory_extraction");
  await form.locator('[name="provider_type"]').selectOption("openai_compatible");
  await form.locator('[name="model_name"]').fill("memorist-e2e-stub");
  await form.locator('[name="endpoint_url"]').fill(STUB_PROVIDER_URL);
  // Memory extraction is a structured-processing role. Declare the JSON
  // capability the controlled provider fixture exercises so certification and
  // runtime resolution prove the same contract.
  await form.locator('[name="supports_json_mode"]').check();
  // Declare the endpoint remote so the privacy boundary applies.
  await form.locator('[name="endpoint_is_local"]').uncheck();
  // Environment-variable NAME, never a key value.
  await form.locator('[name="secret_env_var_name"]').fill("MEMORIST_MEMORY_EXTRACTION_API_KEY");
  await form.locator('button[type="submit"]').click();
  await expect(nodes).toContainText("E2E extraction stub");

  // Remote profiles require explicit privacy acknowledgement.
  const ack = nodes.locator('[data-action="ack"]').first();
  await expect(ack).toBeVisible();
  await ack.click();
  await expect(nodes.locator('[data-action="ack"]')).toHaveCount(0, { timeout: 20_000 });

  // Role-aware provider test against the local stub.
  await nodes.locator('[data-action="test"]').first().click();
  await expect(nodes).toContainText(/ok|healthy|reachable/i, { timeout: 30_000 });

  // Assign as the memory_extraction default.
  await nodes.locator('[name="default_role"]').selectOption("memory_extraction");
  await nodes.locator('[name="default_profile_uuid"]').selectOption({ index: 0 });
  await nodes.locator('[data-action="set-default"]').click();
  await expect(nodes).toContainText(/memory_extraction/);

  const profiles = await apiFetch(page, "/api/v1/memorist/model-control/profiles");
  expect(profiles.status).toBe(200);
  const items = (profiles.body as { items: Array<{ model_profile_uuid: string }> }).items;
  expect(items.length).toBeGreaterThan(0);
  state.profileUuid = items[0].model_profile_uuid;
  saveState(state);
});

test("memory capture: normal chat automatically invokes Memorist", async ({ page, request }) => {
  await page.goto("/");
  await dismissOverlays(page);
  await sendChatMessage(page, CAPTURE_FACT);
  await waitForAssistantReply(page, /stub reply|dog/i);
  state.captureChatId = await currentChatId(page);

  // The toggle is visible and On for this chat.
  const toggle = composerToggle(page);
  await expect(toggle).toBeVisible();
  await expect(toggle).toHaveAttribute("aria-checked", "true");
  await expect(toggle).toContainText(/Memory On/i);

  // The model stub received the turn through the real chat integration.
  const requests = await stubRequests(request);
  expect(requests.length).toBeGreaterThan(0);
  const captureRequest = requests.find((entry) =>
    entry.messages.some((message) => message.content_excerpt.includes("Alpha")),
  );
  expect(captureRequest, "capture turn reached the model stub").toBeTruthy();
  expect(captureRequest?.authorization_header_present).toBeTruthy();

  saveState(state);
});

test("memory retrieval: later chat attaches memory as data and disclosure appears", async ({
  page,
  request,
}) => {
  // Establish the configured origin before reading the authenticated storage
  // state. A fresh Playwright page is otherwise still an opaque about:blank
  // document, where browsers correctly deny localStorage access.
  await page.goto("/", { waitUntil: "domcontentloaded" });

  // Wait on the actor-scoped durable worker state, rather than elapsed time.
  await expect
    .poll(
      async () => {
        const response = await apiFetch(page, "/api/v1/memorist/openwebui/status");
        if (response.status !== 200) return 0;
        const processing = (
          response.body as {
            memory_processing?: { available_messages?: number; candidates?: number };
          }
        ).memory_processing;
        return Math.min(processing?.available_messages ?? 0, processing?.candidates ?? 0);
      },
      { timeout: 30_000 },
    )
    .toBeGreaterThan(0);
  await dismissOverlays(page);
  await sendChatMessage(page, RETRIEVAL_QUESTION);
  await waitForAssistantReply(page, /Alpha/i);
  state.retrievalChatId = await currentChatId(page);

  // The stub must have received the attachment as DATA in the request.
  const requests = await stubRequests(request);
  const retrievalRequest = requests
    .reverse()
    .find((entry) =>
      entry.messages.some(
        (message) =>
          message.role === "user" && message.content_excerpt.trim() === RETRIEVAL_QUESTION,
      ),
    );
  expect(retrievalRequest, "retrieval turn reached the model stub").toBeTruthy();
  const attachmentMessage = retrievalRequest?.messages.find(
    (message) => message.has_memory_context_attachment,
  );
  expect(attachmentMessage, "memory context attachment delivered to the model").toBeTruthy();
  expect(attachmentMessage?.role).toBe("system");
  expect(attachmentMessage?.name).toBe("memorist_context");

  // Truthful "Memory used" disclosure on the assistant turn. The component
  // renders in an open shadow root, which CSS locators pierce.
  const disclosure = page.locator("memorist-memory-attachment").last();
  await expect(disclosure).toBeVisible({ timeout: 30_000 });
  await expect(
    disclosure.locator('span', { hasText: /Memory used/i }).first(),
  ).toBeVisible();

  // Expanding shows safe provenance details, never raw payloads or secrets.
  await disclosure.locator("summary").first().click();
  const disclosureText = await disclosure.evaluate(
    (element) => element.shadowRoot?.textContent || element.textContent || "",
  );
  expect(disclosureText.length).toBeGreaterThan(0);
  expect(disclosureText).not.toContain(STUB_KEY_CANARY);
  expect(disclosureText).not.toMatch(/postgres(ql)?:\/\//i);

  saveState(state);
});

test("memory off: server-side consent ceiling suppresses everything", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await dismissOverlays(page);
  await sendChatMessage(page, "Just say hi.");
  await waitForAssistantReply(page, /stub reply|hi/i);
  state.offChatId = await currentChatId(page);

  const toggle = composerToggle(page);
  await expect(toggle).toHaveAttribute("aria-checked", "true");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-checked", "false", { timeout: 20_000 });
  await expect(toggle).toContainText(/Memory Off/i);

  // Off persists across a reload.
  await page.reload();
  await dismissOverlays(page);
  await expect(composerToggle(page)).toHaveAttribute("aria-checked", "false", {
    timeout: 30_000,
  });

  // Send a unique fact while Off.
  await sendChatMessage(page, OFF_FACT);
  await waitForAssistantReply(page, /stub reply|turtle/i);

  const requests = await stubRequests(request);
  const offRequest = requests.find((entry) =>
    entry.messages.some((message) => message.content_excerpt.includes("Umbra")),
  );
  expect(offRequest, "off turn still reaches the model (without memory)").toBeTruthy();
  expect(
    offRequest?.messages.some((message) => message.has_memory_context_attachment),
  ).toBeFalsy();
  await expect(page.locator("memorist-memory-attachment")).toHaveCount(0);
  state.stubRequestCountAfterOff = requests.length;

  // A forged Full override cannot beat the persisted Off ceiling.
  const forged = await apiFetch(page, "/api/v1/memorist/memory-control/policy/resolve", {
    method: "POST",
    body: {
      chat_uuid: state.offChatId,
      memorist: { turn_policy: "full" },
    },
  });
  expect(forged.status).toBe(200);
  expect((forged.body as { policy?: { mode?: string } }).policy?.mode).toBe("private");

  saveState(state);
});

test("security: no raw secret in DOM, storage, requests, or console", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await dismissOverlays(page);

  const html = await page.content();
  expect(html).not.toContain(STUB_KEY_CANARY);

  const storage = await page.evaluate(() => {
    const dump: Record<string, string | null> = {};
    for (let i = 0; i < localStorage.length; i += 1) {
      const key = localStorage.key(i)!;
      dump[`local:${key}`] = localStorage.getItem(key);
    }
    for (let i = 0; i < sessionStorage.length; i += 1) {
      const key = sessionStorage.key(i)!;
      dump[`session:${key}`] = sessionStorage.getItem(key);
    }
    return JSON.stringify(dump);
  });
  expect(storage).not.toContain(STUB_KEY_CANARY);
  expect(consoleLines.join("\n")).not.toContain(STUB_KEY_CANARY);

  // The stub's safe request log must not have persisted the raw key either.
  const requests = await stubRequests(request);
  expect(JSON.stringify(requests)).not.toContain(STUB_KEY_CANARY);

  // Raw and nested secret fields are rejected by the profile API.
  for (const body of [
    { profile_name: "x", role: "memory_extraction", provider_type: "openai_compatible", model_name: "m", api_key: "sk-raw" },
    { profile_name: "x", role: "memory_extraction", provider_type: "openai_compatible", model_name: "m", nested: { provider_token: "sk-raw" } },
  ]) {
    const rejected = await apiFetch(page, "/api/v1/memorist/model-control/profiles", {
      method: "POST",
      body,
    });
    expect(rejected.status).toBe(422);
  }
});

test("actor isolation: a second user cannot reach admin or foreign state", async ({
  browser,
  page,
}) => {
  // Give the default admin page a real application origin before apiFetch
  // reads its authenticated localStorage state.
  await page.goto("/", { waitUntil: "domcontentloaded" });

  // Admin creates the member account.
  const created = await apiFetch(page, "/api/v1/auths/add", {
    method: "POST",
    body: { ...state.member, role: "user" },
  });
  expect(created.status).toBe(200);
  saveState(state);

  const memberContext = await browser.newContext();
  const memberPage = await memberContext.newPage();
  const { signIn } = await import("./helpers");
  await signIn(memberPage, state.member);

  // Admin-only surfaces stay admin-only.
  const setup = await apiFetch(memberPage, "/api/v1/memorist/model-control/setup/status");
  expect(setup.status).toBe(403);
  const diagnostics = await apiFetch(memberPage, "/api/v1/memorist/openwebui/diagnostics");
  expect(diagnostics.status).toBe(403);
  await memberPage.goto("/settings/memorist/memory-setup");
  await expect(memberPage.locator("[data-memorist-unauthorized]")).toBeVisible();

  // The member sees their own default workflow state for the admin's chat
  // scope identifier — never the admin's persisted Off.
  const foreign = await apiFetch(
    memberPage,
    `/api/v1/memorist/memory-control/workflow/chats/${state.offChatId}`,
  );
  expect(foreign.status).toBe(200);
  expect((foreign.body as { memory_enabled?: boolean }).memory_enabled).toBe(true);

  // The member cannot read the admin's attachment disclosure state.
  const disclosure = await apiFetch(
    memberPage,
    "/api/v1/memorist/memory-control/messages/any-admin-message/attachment",
  );
  expect(disclosure.status).toBe(200);
  expect((disclosure.body as { status?: string }).status).toBe("none");

  await memberContext.close();
});
