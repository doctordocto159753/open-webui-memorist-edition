import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  MemoristMemoryAttachment,
  mountMemoryAttachmentForMessage,
  redactAttachmentText,
} from "../../open-webui-integration/memorist/ui/memoryAttachment";
import type {
  MemoristAttachmentDisplayItem,
  MemoristAttachmentPreview,
} from "../../open-webui-integration/memorist/ui/memoristClient";

const auditItem: MemoristAttachmentDisplayItem = {
  id: "version-1",
  title: "Use concise answers",
  summary: "Use concise answers",
  memory_type: "preference",
  scope_type: "project",
  source_authority: "user_explicit",
  explicitness: "explicit",
  status: "accepted",
  confidence: 0.91,
  retrieval_score: 0.87,
  reason: "Relevant to this turn",
  created_at: "2026-07-01T12:00:00Z",
  semantic_authority: "canonical_jakobson_gate_route",
  gate_decision: "analyze",
  route_type: "preference",
  route_status: "selected",
  metadata_versions: {
    route_mapping_version: "route-v1",
    candidate_service_version: "candidate-v1",
    provenance_policy_version: "provenance-v1",
  },
  technical_ids: {
    memory_uuid: "memory-1",
    memory_version_uuid: "version-1",
    source_candidate_uuid: "candidate-1",
    route_uuid: "route-1",
    annotation_uuid: "annotation-1",
  },
};

function preview(items: MemoristAttachmentDisplayItem[]): MemoristAttachmentPreview {
  return {
    attachment_uuid: "attachment-1",
    session_uuid: "session-1",
    input_message_uuid: "message-1",
    retrieval_run_uuid: "retrieval-1",
    token_count: 42,
    lifecycle_status: "delivered",
    delivery_state: "delivered",
    user_disposition: "none",
    attachment_review: false,
    generation: 1,
    display_version: "pr5a-v1",
    memory_count: items.length,
    items,
  };
}

function client(items: MemoristAttachmentDisplayItem[]) {
  return { previewAttachment: vi.fn().mockResolvedValue(preview(items)) };
}

async function mountedText(element: MemoristMemoryAttachment): Promise<string> {
  await vi.waitFor(() => expect(element.shadowRoot?.textContent).not.toContain("Loading"));
  return element.shadowRoot?.textContent ?? "";
}

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("memory attachment chat surface", () => {
  it("renders multiple memories with readable labels", async () => {
    const root = document.createElement("div");
    document.body.append(root);
    const displayClient = client([
      auditItem,
      {
        ...auditItem,
        id: "version-2",
        summary: "Build is verified by the import tool",
        memory_type: "tool_observation",
        scope_type: "workspace",
        source_authority: "tool_observation",
        explicitness: "observed",
      },
    ]);

    const element = mountMemoryAttachmentForMessage(
      root,
      { memorist_delivered_attachment_uuid: "attachment-1" },
      displayClient,
    );
    expect(element).toBeDefined();
    const text = await mountedText(element!);

    expect(text).toContain("Memory used");
    expect(text).toContain("2 memories attached");
    expect(text).toContain("User preference");
    expect(text).toContain("Project");
    expect(text).toContain("Explicitly provided by you");
    expect(text).toContain("Tool observation");
    expect(text).toContain("Workspace");
  });

  it("keeps no-attachment and empty responses quiet", async () => {
    const root = document.createElement("div");
    document.body.append(root);

    expect(mountMemoryAttachmentForMessage(root, {})).toBeUndefined();
    expect(root.childElementCount).toBe(0);

    const element = mountMemoryAttachmentForMessage(
      root,
      { memorist_delivered_attachment_uuid: "attachment-empty" },
      client([]),
    )!;
    await mountedText(element);
    expect(element.hidden).toBe(true);
    expect(element.shadowRoot?.querySelector(".group")).toBeNull();
    expect(element.shadowRoot?.textContent).not.toContain("Memory used");
  });

  it("does not render sensitive values or raw metadata in the normal view", async () => {
    const root = document.createElement("div");
    document.body.append(root);
    const element = mountMemoryAttachmentForMessage(
      root,
      { memorist_delivered_attachment_uuid: "attachment-sensitive" },
      client([
        {
          ...auditItem,
          id: "version-sensitive",
          summary: "password=hunter2",
          sensitive: true,
          memory_type: "fact",
          source_authority: "imported_record",
        },
      ]),
    )!;
    const text = await mountedText(element);

    expect(text).toContain("Sensitive memory — content hidden");
    expect(text).toContain("Imported record");
    expect(text).not.toContain("hunter2");
    expect(element.shadowRoot?.innerHTML).not.toContain("object_ijson");
    expect(element.shadowRoot?.innerHTML).not.toContain("extraction_metadata_ijson");
  });

  it("includes structured route and provenance details in the expandable audit view", async () => {
    const root = document.createElement("div");
    document.body.append(root);
    const element = mountMemoryAttachmentForMessage(
      root,
      { memorist_delivered_attachment_uuid: "attachment-1" },
      client([auditItem]),
    )!;
    const text = await mountedText(element);

    expect(text).toContain("Provenance and audit details");
    expect(text).toContain("route-1");
    expect(text).toContain("annotation-1");
    expect(text).toContain("provenance-v1");
    expect(text).toContain("canonical_jakobson_gate_route");
    const itemsHtml = element.shadowRoot?.querySelector(".items")?.innerHTML ?? "";
    expect(itemsHtml).not.toContain("{");
  });

  it("fails safely when preview retrieval errors", async () => {
    const root = document.createElement("div");
    document.body.append(root);
    const failingClient = {
      previewAttachment: vi.fn().mockRejectedValue(new Error("Bearer top-secret")),
    };
    const element = mountMemoryAttachmentForMessage(
      root,
      { memorist_delivered_attachment_uuid: "attachment-error" },
      failingClient,
    )!;
    const text = await mountedText(element);

    expect(text).toContain("Memory details are unavailable");
    expect(text).not.toContain("top-secret");
  });

  it("masks secrets again at the frontend boundary", () => {
    expect(redactAttachmentText("token=abcdefghijk")).toBe("[redacted]");
    expect(redactAttachmentText("raw secret", true)).toBe(
      "Sensitive memory — content hidden",
    );
  });
});
