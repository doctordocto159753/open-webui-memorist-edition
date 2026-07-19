/**
 * PR5-G phase 2: runs after CI stops and starts the stack through the
 * packaged lifecycle scripts (Stop-Memorist.ps1 / Start-Memorist.ps1).
 * Verifies restart persistence and regeneration idempotency against the same
 * data volumes.
 */
import { expect, test } from "@playwright/test";

import {
  composerToggle,
  dismissOverlays,
  loadState,
  signIn,
  stubRequests,
  waitForAssistantReply,
  sendChatMessage,
} from "./helpers";

const state = loadState();
const RETRIEVAL_QUESTION = "What is my dog's name and preferred food?";

function primaryRetrievalRequestCount(
  requests: Awaited<ReturnType<typeof stubRequests>>,
): number {
  return requests.filter((entry) =>
    entry.messages.some(
      (message) =>
        message.role === "user" && message.content_excerpt.trim() === RETRIEVAL_QUESTION,
    ),
  ).length;
}

test.describe.configure({ mode: "serial" });

test("restart persistence: chats, settings, profiles, and memory survive", async ({
  page,
  request,
}) => {
  await signIn(page, state.admin);

  // Prior chats persist.
  expect(state.captureChatId, "phase 1 must have recorded the capture chat").toBeTruthy();
  await page.goto(`/c/${state.captureChatId}`);
  await expect(page.locator("body")).toContainText(/Alpha/i, { timeout: 30_000 });

  // The persisted Off chat is still Off — the consent ceiling survived restart.
  await page.goto(`/c/${state.offChatId}`);
  await expect(composerToggle(page)).toHaveAttribute("aria-checked", "false", {
    timeout: 30_000,
  });

  // Processing profile and privacy acknowledgement persist.
  await page.goto("/settings/memorist/processing-nodes");
  const nodes = page.locator("memorist-processing-nodes-settings");
  await expect(nodes).toContainText("E2E extraction stub");
  await expect(nodes.locator('[data-action="ack"]')).toHaveCount(0);

  // The original memory is still retrievable in a brand-new chat.
  await page.goto("/");
  await dismissOverlays(page);
  await sendChatMessage(page, "Remind me: what is my dog's name and preferred food?");
  await waitForAssistantReply(page, /Alpha/i);
  const disclosure = page.locator("memorist-memory-attachment").last();
  await expect(disclosure).toBeVisible({ timeout: 30_000 });
  await expect(
    disclosure.locator('span', { hasText: /Memory used/i }).first(),
  ).toBeVisible();
});

test("regeneration: idempotent, truthful, no duplicate capture", async ({ page, request }) => {
  await signIn(page, state.admin);
  await page.goto(`/c/${state.retrievalChatId}`);
  await dismissOverlays(page);
  await expect(page.locator("body")).toContainText(/Alpha/i, { timeout: 30_000 });

  const requestsBefore = await stubRequests(request);
  const primaryRequestsBefore = primaryRetrievalRequestCount(requestsBefore);

  // Trigger regeneration of the last assistant response.
  const regenerateMenu = page.locator('[aria-label="Regenerate"]:visible').last();
  await expect(regenerateMenu).toBeVisible();
  await regenerateMenu.click();
  const tryAgain = page.getByRole("button", { name: "Try Again", exact: true });
  await expect(tryAgain).toBeVisible();
  await tryAgain.click();

  // Wait on the observable model-stub request, not elapsed wall-clock time.
  await expect
    .poll(async () => primaryRetrievalRequestCount(await stubRequests(request)))
    .toBe(primaryRequestsBefore + 1);
  await waitForAssistantReply(page, /Alpha/i);

  // Exactly one new primary model request for the regenerated turn. Open
  // WebUI may independently issue auxiliary follow-up/tag/title tasks.
  const requestsAfter = await stubRequests(request);
  expect(primaryRetrievalRequestCount(requestsAfter)).toBe(primaryRequestsBefore + 1);

  // The regenerated turn shows at most one truthful disclosure — never a
  // duplicated or stale one.
  const disclosures = page.locator("memorist-memory-attachment");
  const count = await disclosures.count();
  expect(count).toBeLessThanOrEqual(1);
});
