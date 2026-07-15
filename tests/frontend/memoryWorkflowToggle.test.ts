import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  applyMemoryWorkflowToRequest,
  applyOriginalMemoryWorkflowToRegeneration,
  mountMemoryWorkflowToggleNearComposer,
} from "../../open-webui-integration/memorist/ui/memoryWorkflowToggle";
import type { MemoryWorkflowState } from "../../open-webui-integration/memorist/ui/memoristClient";

function workflowState(
  state: "on" | "off" | "limited" = "on",
): MemoryWorkflowState {
  const enabled = state === "on";
  return {
    chat_uuid: "chat-1",
    session_uuid: null,
    memory_enabled: enabled,
    capture_enabled: enabled,
    recall_enabled: enabled,
    attachment_enabled: enabled,
    state,
    scope: "chat",
    source: state === "on" ? "system" : "chat",
    persisted: state !== "on",
    updated_at: null,
    regeneration_policy: "original_turn",
  };
}

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("memory workflow composer toggle", () => {
  it("loads and renders the current per-chat state accessibly", async () => {
    const root = document.createElement("div");
    document.body.append(root);
    const client = {
      getMemoryWorkflowState: vi.fn().mockResolvedValue(workflowState("on")),
      setMemoryWorkflowState: vi.fn(),
    };

    const element = mountMemoryWorkflowToggleNearComposer(
      root,
      "chat-1",
      client,
    )!;
    await vi.waitFor(() => {
      expect(element.shadowRoot?.textContent).toContain("Memory On");
    });

    const button = element.shadowRoot?.querySelector("button");
    expect(button?.getAttribute("role")).toBe("switch");
    expect(button?.getAttribute("aria-checked")).toBe("true");
    expect(element.shadowRoot?.textContent).toContain(
      "relevant memories may be attached",
    );
    expect(client.getMemoryWorkflowState).toHaveBeenCalledWith("chat-1");
  });

  it("persists off and explains both capture and attachment behavior", async () => {
    const root = document.createElement("div");
    document.body.append(root);
    const client = {
      getMemoryWorkflowState: vi.fn().mockResolvedValue(workflowState("on")),
      setMemoryWorkflowState: vi.fn().mockResolvedValue(workflowState("off")),
    };
    const element = mountMemoryWorkflowToggleNearComposer(root, "chat-1", client)!;
    await vi.waitFor(() => {
      expect(element.shadowRoot?.textContent).toContain("Memory On");
    });

    (element.shadowRoot?.querySelector("button") as HTMLButtonElement).click();
    await vi.waitFor(() => {
      expect(element.shadowRoot?.textContent).toContain("Memory Off");
    });

    expect(client.setMemoryWorkflowState).toHaveBeenCalledWith("chat-1", false);
    expect(element.shadowRoot?.textContent).toContain("won’t be saved");
    expect(element.shadowRoot?.textContent).toContain("won’t be attached");
    expect(
      element.shadowRoot?.querySelector("button")?.getAttribute("aria-checked"),
    ).toBe("false");
  });

  it("rolls back optimistic state and shows a safe error", async () => {
    const root = document.createElement("div");
    document.body.append(root);
    const client = {
      getMemoryWorkflowState: vi.fn().mockResolvedValue(workflowState("on")),
      setMemoryWorkflowState: vi.fn().mockRejectedValue(
        new Error("Bearer secret-value"),
      ),
    };
    const element = mountMemoryWorkflowToggleNearComposer(root, "chat-1", client)!;
    await vi.waitFor(() => {
      expect(element.shadowRoot?.textContent).toContain("Memory On");
    });

    (element.shadowRoot?.querySelector("button") as HTMLButtonElement).click();
    await vi.waitFor(() => {
      expect(element.shadowRoot?.textContent).toContain("previous setting is still active");
    });

    expect(element.shadowRoot?.textContent).toContain("Memory On");
    expect(element.shadowRoot?.textContent).not.toContain("secret-value");
  });

  it("is disabled while loading and remains compact on mobile", () => {
    const root = document.createElement("div");
    document.body.append(root);
    const client = {
      getMemoryWorkflowState: vi.fn().mockReturnValue(new Promise(() => undefined)),
      setMemoryWorkflowState: vi.fn(),
    };
    const element = mountMemoryWorkflowToggleNearComposer(root, "chat-1", client)!;

    expect(element.shadowRoot?.textContent).toContain("Loading Memory");
    expect(
      (element.shadowRoot?.querySelector("button") as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(element.shadowRoot?.innerHTML).toContain("@media (max-width: 34rem)");
  });

  it("adds truthful send metadata and preserves the original regeneration state", () => {
    const onRequest = applyMemoryWorkflowToRequest(
      { messages: [], metadata: { existing: "kept" } },
      workflowState("on"),
    );
    expect(onRequest.memorist).toMatchObject({ turn_policy: "full" });
    expect(onRequest.metadata).toMatchObject({
      existing: "kept",
      memorist_memory_workflow: "on",
      memorist_capture_enabled: true,
      memorist_attachment_enabled: true,
    });

    const regeneration = applyOriginalMemoryWorkflowToRegeneration(
      { messages: [] },
      {
        memorist_memory_workflow: "off",
        memorist_capture_enabled: false,
        memorist_recall_enabled: false,
        memorist_attachment_enabled: false,
      },
      workflowState("on"),
    );
    expect(regeneration.memorist).toMatchObject({ turn_policy: "private" });
    expect(regeneration.metadata).toMatchObject({
      memorist_memory_workflow: "off",
      memorist_capture_enabled: false,
      memorist_recall_enabled: false,
      memorist_attachment_enabled: false,
    });
  });
});
