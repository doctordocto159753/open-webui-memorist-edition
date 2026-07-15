import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  mountMemoryNodeSetup,
} from "../../open-webui-integration/memorist/ui/memoryNodeSetup";
import type {
  MemoryNodeSetupStatus,
} from "../../open-webui-integration/memorist/ui/memoristClient";

function setupStatus(configured = false): MemoryNodeSetupStatus {
  return {
    memory_setup_required: false,
    ready_for_memory_processing: true,
    recommended_setup: !configured,
    configured_roles: configured ? ["memory_extraction"] : [],
    fallback_roles: configured ? [] : ["memory_extraction", "high_confidence_extraction"],
    missing_roles: [],
    recommended_missing_roles: configured ? [] : ["memory_extraction"],
    local_fallback_available: true,
    runtime_profile: "lite",
    scope: "workspace",
    secret_strategy: "env_var_reference",
    secret_values_returned: false,
    full_mode_note: null,
    roles: [
      {
        role: "memory_extraction",
        title: "Memory Extraction Model",
        required: true,
        recommended: true,
        configured,
        available: true,
        source: configured ? "configured_default" : "built_in_fallback",
        provider_type: configured ? "openai_compatible_llm" : "deterministic",
        provider_name: configured ? "Custom OpenAI-compatible endpoint" : "deterministic",
        model_name: configured ? "example-memory-model" : "deterministic_extraction",
        model_profile_uuid: configured ? "profile-1" : null,
        endpoint_is_local: !configured,
        secret_configured: configured,
        supports_structured_output: configured,
        supports_embeddings: false,
        description: "Extracts memories.",
        safe_fallback: "deterministic extraction",
      },
      {
        role: "high_confidence_extraction",
        title: "High-confidence Extraction Model",
        required: false,
        recommended: true,
        configured: false,
        available: true,
        source: "built_in_fallback",
        provider_type: "deterministic",
        provider_name: "deterministic",
        model_name: "inherits-memory-extraction",
        model_profile_uuid: null,
        endpoint_is_local: true,
        secret_configured: false,
        supports_structured_output: false,
        supports_embeddings: false,
        description: "Optional stricter extraction.",
        safe_fallback: "inherits extraction",
      },
    ],
  };
}

function client() {
  return {
    memoryNodeSetupStatus: vi
      .fn()
      .mockResolvedValueOnce(setupStatus(false))
      .mockResolvedValue(setupStatus(true)),
    createModelControlProfile: vi.fn().mockResolvedValue({
      model_profile_uuid: "profile-1",
      secret_configured: true,
    }),
    testModelControlProfile: vi.fn().mockResolvedValue({
      model_profile_uuid: "profile-1",
      health: {
        status: "ok",
        provider_type: "openai_compatible_llm",
        model_name: "example-memory-model",
        latency_ms: 12,
        local_only_safe: false,
        detail: "HTTP 200",
      },
    }),
    setModelControlDefault: vi.fn().mockResolvedValue({
      role: "memory_extraction",
      model_profile_uuid: "profile-1",
      reindex_required: false,
    }),
  };
}

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("first-run memory node setup", () => {
  it("renders truthful local fallback and supported roles", async () => {
    const root = document.createElement("div");
    document.body.append(root);
    const element = mountMemoryNodeSetup(root, client());

    await vi.waitFor(() => {
      expect(element.shadowRoot?.textContent).toContain("Local fallback available");
    });
    expect(element.shadowRoot?.textContent).toContain("Memory extraction");
    expect(element.shadowRoot?.textContent).toContain("High-confidence extraction");
    expect(element.shadowRoot?.textContent).toContain("no API key");
    expect(element.shadowRoot?.innerHTML).toContain("@media(max-width:36rem)");
  });

  it("local deterministic mode saves without endpoint or secret", async () => {
    const mock = client();
    const root = document.createElement("div");
    document.body.append(root);
    const element = mountMemoryNodeSetup(root, mock);
    await vi.waitFor(() => {
      expect(element.shadowRoot?.textContent).toContain("Local fallback available");
    });

    (element.shadowRoot?.querySelector("form") as HTMLFormElement).requestSubmit();
    await vi.waitFor(() => {
      expect(mock.setModelControlDefault).toHaveBeenCalled();
    });

    expect(mock.createModelControlProfile).toHaveBeenCalledWith(
      expect.objectContaining({
        provider_type: "deterministic",
        secret_strategy: "none",
        secret_env_var_name: null,
      }),
    );
  });

  it("requires remote endpoint, model, env reference, and privacy acknowledgement", async () => {
    const mock = client();
    const root = document.createElement("div");
    document.body.append(root);
    const element = mountMemoryNodeSetup(root, mock);
    await vi.waitFor(() => {
      expect(element.shadowRoot?.textContent).toContain("Local fallback available");
    });

    const mode = element.shadowRoot?.querySelector(
      '[name="provider_mode"]',
    ) as HTMLSelectElement;
    mode.value = "openai_compatible";
    mode.dispatchEvent(new Event("change"));

    (element.shadowRoot?.querySelector("form") as HTMLFormElement).requestSubmit();
    await vi.waitFor(() => {
      expect(element.shadowRoot?.textContent).toContain(
        "Endpoint URL and model name are required",
      );
    });
    expect(mock.createModelControlProfile).not.toHaveBeenCalled();
    expect(element.shadowRoot?.querySelector('[name="api_key"]')).toBeNull();
    expect(element.shadowRoot?.textContent).toContain(
      "enter only its variable name",
    );
  });

  it("tests, assigns, and masks a remote provider without retaining a key value", async () => {
    const mock = client();
    const root = document.createElement("div");
    document.body.append(root);
    const element = mountMemoryNodeSetup(root, mock);
    await vi.waitFor(() => {
      expect(element.shadowRoot?.textContent).toContain("Local fallback available");
    });

    const mode = element.shadowRoot?.querySelector(
      '[name="provider_mode"]',
    ) as HTMLSelectElement;
    mode.value = "openai_compatible";
    mode.dispatchEvent(new Event("change"));

    (element.shadowRoot?.querySelector('[name="model_name"]') as HTMLInputElement).value =
      "example-memory-model";
    (element.shadowRoot?.querySelector('[name="endpoint_url"]') as HTMLInputElement).value =
      "https://provider.example/v1";
    (
      element.shadowRoot?.querySelector(
        '[name="secret_env_var_name"]',
      ) as HTMLInputElement
    ).value = "MEMORIST_MEMORY_EXTRACTION_API_KEY";
    (
      element.shadowRoot?.querySelector(
        '[name="privacy_acknowledged"]',
      ) as HTMLInputElement
    ).checked = true;

    (element.shadowRoot?.querySelector("form") as HTMLFormElement).requestSubmit();
    await vi.waitFor(() => {
      expect(element.shadowRoot?.textContent).toContain("is configured");
    });

    expect(mock.testModelControlProfile).toHaveBeenCalledWith(
      "profile-1",
      { timeout_ms: 3000 },
    );
    expect(mock.setModelControlDefault).toHaveBeenCalledWith({
      role: "memory_extraction",
      model_profile_uuid: "profile-1",
    });
    expect(element.shadowRoot?.textContent).not.toContain("sk-example-secret");
    expect(element.shadowRoot?.textContent).toContain(
      "Secret values were not stored or returned",
    );
  });

  it("redacts provider errors before rendering", async () => {
    const mock = client();
    mock.testModelControlProfile.mockResolvedValue({
      model_profile_uuid: "profile-1",
      health: {
        status: "error",
        provider_type: "deterministic",
        model_name: "local",
        latency_ms: 0,
        local_only_safe: true,
        detail: "Bearer raw-token api_key=raw-key",
      },
    });
    const root = document.createElement("div");
    document.body.append(root);
    const element = mountMemoryNodeSetup(root, mock);
    await vi.waitFor(() => {
      expect(element.shadowRoot?.textContent).toContain("Local fallback available");
    });

    (element.shadowRoot?.querySelector("form") as HTMLFormElement).requestSubmit();
    await vi.waitFor(() => {
      expect(element.shadowRoot?.textContent).toContain("connection test failed");
    });
    expect(element.shadowRoot?.textContent).not.toContain("raw-token");
    expect(element.shadowRoot?.textContent).not.toContain("raw-key");
  });
});
