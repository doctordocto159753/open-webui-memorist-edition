import { beforeEach, describe, expect, it, vi } from "vitest";

import { MemoristProcessingNodesSettings } from "../../open-webui-integration/memorist/ui/processingNodes";

const profile = {
  model_profile_uuid: "profile-certified",
  profile_name: "Certified memory provider",
  role: "memory_extraction",
  provider_type: "openai_compatible_llm",
  model_name: "memory-model",
  endpoint_url: "https://provider.example/v1",
  endpoint_is_local: false,
  privacy_acknowledged_at: "2026-01-01T00:00:00Z",
  secret_configured: true,
  secret_reference_configured: true,
  secret_available_in_core: true,
  authentication_status: "valid",
  certification_status: "current",
  certification_current: true,
  is_enabled: true,
} as const;

function client() {
  return {
    modelControlProfiles: vi.fn().mockResolvedValue({ items: [profile] }),
    modelControlDefaults: vi.fn().mockResolvedValue({
      items: [{
        role: "memory_extraction",
        model_profile_uuid: profile.model_profile_uuid,
      }],
    }),
    modelControlHealth: vi.fn().mockResolvedValue({
      status: "ok",
      latest_health_events: [],
    }),
    modelControlEffective: vi.fn().mockResolvedValue({
      resolution_version: "processing-role-resolution-v1",
      items: [{
        role: "memory_extraction",
        requested_role: "memory_extraction",
        effective_role: "memory_extraction",
        model_profile_uuid: profile.model_profile_uuid,
        provider_type: profile.provider_type,
        model_name: profile.model_name,
        endpoint_is_local: false,
        scope_source: "global",
        capability_compatible: true,
        capability_reasons: [],
      }],
    }),
    setModelControlDefault: vi.fn().mockResolvedValue({
      role: "memory_extraction",
      model_profile_uuid: profile.model_profile_uuid,
      reindex_required: false,
    }),
    removeModelControlDefault: vi.fn().mockResolvedValue({
      role: "memory_extraction",
      removed: true,
      reindex_required: false,
    }),
  };
}

async function mount(mock: ReturnType<typeof client>) {
  const element = new MemoristProcessingNodesSettings();
  Object.defineProperty(element, "client", {
    configurable: true,
    value: mock,
  });
  document.body.append(element);
  await vi.waitFor(() => {
    expect(element.textContent).toContain("Certified memory provider");
  });
  return element;
}

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("processing-node server authority", () => {
  it("uses persisted certification after refresh and can replace the default", async () => {
    const mock = client();
    const element = await mount(mock);
    const role = element.querySelector('[name="default_role"]') as HTMLSelectElement;
    role.value = "memory_extraction";
    const selected = element.querySelector(
      '[name="default_profile_uuid"]',
    ) as HTMLSelectElement;
    selected.value = profile.model_profile_uuid;

    (element.querySelector('[data-action="set-default"]') as HTMLButtonElement).click();

    await vi.waitFor(() => {
      expect(mock.setModelControlDefault).toHaveBeenCalledWith({
        role: "memory_extraction",
        model_profile_uuid: profile.model_profile_uuid,
      });
    });
    expect(element.textContent).not.toContain("Test this exact profile successfully");
  });

  it("removes a persisted role default through the backend contract", async () => {
    const mock = client();
    const element = await mount(mock);
    (
      element.querySelector(
        '[data-action="remove-default"][data-role="memory_extraction"]',
      ) as HTMLButtonElement
    ).click();

    await vi.waitFor(() => {
      expect(mock.removeModelControlDefault).toHaveBeenCalledWith(
        "memory_extraction",
      );
    });
  });
});
