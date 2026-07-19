import { describe, expect, it } from "vitest";

import {
  MemoristMessageDisclosure,
  mountMessageDisclosure,
} from "../../open-webui-integration/memorist/ui/messageDisclosure";
import type { MemoristMessageAttachmentDisclosure } from "../../open-webui-integration/memorist/ui/memoristClient";

function client(responses: Record<string, MemoristMessageAttachmentDisclosure>) {
  const calls: string[] = [];
  return {
    calls,
    async messageAttachmentDisclosure(messageId: string) {
      calls.push(messageId);
      const found = responses[messageId];
      if (!found) return { status: "none", attachment_uuid: null, disclose: false };
      return found;
    },
  };
}

async function flush(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

describe("memorist-message-disclosure", () => {
  it("mounts the attachment component only when delivery truth discloses", async () => {
    const fake = client({
      "user-msg-1": { status: "delivered", attachment_uuid: "attachment-9", disclose: true },
    });
    const container = document.createElement("div");
    document.body.append(container);
    const element = mountMessageDisclosure(
      container,
      { messageId: "assistant-msg-1", parentMessageId: "user-msg-1", done: true },
      fake,
    );
    await flush();
    expect(fake.calls[0]).toBe("user-msg-1");
    const attachment = element.querySelector("memorist-memory-attachment");
    expect(attachment).not.toBeNull();
    expect((attachment as { attachmentUuid?: string }).attachmentUuid).toBe("attachment-9");
  });

  it("stays visually quiet when no attachment was delivered", async () => {
    const fake = client({});
    const container = document.createElement("div");
    document.body.append(container);
    const element = mountMessageDisclosure(
      container,
      { messageId: "assistant-msg-2", parentMessageId: "user-msg-2", done: true },
      fake,
    );
    await flush();
    expect(fake.calls).toEqual(["user-msg-2", "assistant-msg-2"]);
    expect(element.children.length).toBe(0);
  });

  it("renders nothing while the assistant turn is still generating", async () => {
    const fake = client({
      "user-msg-3": { status: "delivered", attachment_uuid: "attachment-3", disclose: true },
    });
    const container = document.createElement("div");
    document.body.append(container);
    const element = mountMessageDisclosure(
      container,
      { messageId: "assistant-msg-3", parentMessageId: "user-msg-3", done: false },
      fake,
    );
    await flush();
    expect(element.children.length).toBe(0);
    // Completion re-queries and mounts truthfully.
    element.setAttribute("message-done", "true");
    await flush();
    expect(element.querySelector("memorist-memory-attachment")).not.toBeNull();
  });

  it("swallows lookup failures without disturbing the chat", async () => {
    const failing = {
      async messageAttachmentDisclosure(): Promise<MemoristMessageAttachmentDisclosure> {
        throw new Error("proxy unavailable");
      },
    };
    const container = document.createElement("div");
    document.body.append(container);
    const element = mountMessageDisclosure(
      container,
      { messageId: "assistant-msg-4", parentMessageId: "user-msg-4", done: true },
      failing,
    );
    await flush();
    expect(element.children.length).toBe(0);
  });

  it("does not reuse stale disclosure state across regeneration re-renders", async () => {
    const responses: Record<string, MemoristMessageAttachmentDisclosure> = {
      "user-msg-5": { status: "delivered", attachment_uuid: "attachment-5", disclose: true },
    };
    const fake = client(responses);
    const container = document.createElement("div");
    document.body.append(container);
    const element = mountMessageDisclosure(
      container,
      { messageId: "assistant-msg-5", parentMessageId: "user-msg-5", done: true },
      fake,
    );
    await flush();
    expect(element.querySelector("memorist-memory-attachment")).not.toBeNull();
    // Regenerated turn without recall: persisted truth now says no delivery.
    delete responses["user-msg-5"];
    element.setAttribute("message-done", "true");
    await element.refresh();
    await flush();
    expect(element.querySelector("memorist-memory-attachment")).toBeNull();
  });

  it("registers the custom element", () => {
    expect(customElements.get("memorist-message-disclosure")).toBe(MemoristMessageDisclosure);
  });
});
