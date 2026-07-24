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
        scope_source: configured ? "workspace" : "built_in_fallback",
        inheritance_source: null,
        fallback_reason: configured ? null : "no_configured_default",
        effective_role: "memory_extraction",
        provider_type: configured ? "openai_compatible_llm" : "deterministic",
        provider_name: configured ? "Custom OpenAI-compatible endpoint" : "deterministic",
        model_name: configured ? "example-memory-model" : "deterministic_extraction",
        model_profile_uuid: configured ? "profile-1" : null,
        endpoint_is_local: !configured,
        secret_configured: configured,
        secret_available: true,
        privacy_acknowledged: true,
        capability_compatible: true,
        capability_reasons: [],
        supports_structured_output: configured,
        supports_embeddings: false,
        description: "Extracts memories.",
        safe_fallback: "deterministic extraction",
        runtime_wired: true,
        last_health: null,
        last_runtime_use: null,
      },
      {
        role: "high_confidence_extraction",
        title: "High-confidence Extraction Model",
        required: false,
        recommended: true,
        configured: false,
        available: true,
        source: "built_in_fallback",
        scope_source: "built_in_fallback",
        inheritance_source: null,
        fallback_reason: "no_configured_default",
        effective_role: "high_confidence_extraction",
        provider_type: "deterministic",
        provider_name: "deterministic",
        model_name: "inherits-memory-extraction",
        model_profile_uuid: null,
        endpoint_is_local: true,
        secret_configured: false,
        secret_available: true,
        privacy_acknowledged: true,
        capability_compatible: true,
        capability_reasons: [],
        supports_structured_output: false,
        supports_embeddings: false,
        description: "Optional stricter extraction.",
        safe_fallback: "inherits extraction",
        runtime_wired: true,
        last_health: null,
        last_runtime_use: null,
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
        dns_or_host_reachable: "reachable",
        tcp_or_http_reachable: "reachable",
        authentication_status: "valid",
        model_status: "available",
        chat_completion_status: "supported",
        structured_output_status: "supported",
        role_compatibility_status: "compatible",
        overall_status: "ok",
        retryable: false,
        quota_or_rate_limited: false,
        detail_sanitized: "HTTP 200",
      },
      timeout_ms: 15000,
      test_levels: {
        connectivity_and_authentication: {
          host: "reachable",
          http: "reachable",
          authentication: "valid",
        },
        model_capability: {
          model: "available",
          chat_completion: "supported",
          structured_output: "supported",
        },
        role_compatibility: "compatible",
      },
    }),
    setModelControlDefault: vi.fn().mockResolvedValue({
      role: "memory_extraction",
      model_profile_uuid: "profile-1",
      reindex_required: false,
    }),
    modelControlEffective: vi.fn().mockResolvedValue({
      resolution_version: "processing-role-resolution-v1",
      items: [{
        role: "memory_extraction",
        requested_role: "memory_extraction",
        effective_role: "memory_extraction",
        model_profile_uuid: "profile-1",
        provider_type: "openai_compatible_llm",
        model_name: "example-memory-model",
        endpoint_is_local: false,
        scope_source: "workspace",
        inheritance_source: null,
        fallback_reason: null,
        capability_compatible: true,
        capability_reasons: [],
      }],
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
      { idempotency_key: "memorist-setup-v1:memory_extraction:openai_compatible:test" },
    );
    expect(mock.setModelControlDefault).toHaveBeenCalledWith({
      role: "memory_extraction",
      model_profile_uuid: "profile-1",
    });
    expect(element.shadowRoot?.textContent).not.toContain("sk-" + "example-secret");
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
        dns_or_host_reachable: "reachable",
        tcp_or_http_reachable: "reachable",
        authentication_status: "valid",
        model_status: "available",
        chat_completion_status: "supported",
        structured_output_status: "unsupported",
        role_compatibility_status: "incompatible",
        overall_status: "incompatible",
        retryable: false,
        quota_or_rate_limited: false,
        detail_sanitized: "Bearer raw-token api_key=raw-key",
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
      expect(element.shadowRoot?.textContent).toContain("Profile saved for editing");
    });
    expect(element.shadowRoot?.textContent).not.toContain("raw-token");
    expect(element.shadowRoot?.textContent).not.toContain("raw-key");
  });

  it.each([
    {
      overall: "rate_limited",
      authentication: "valid",
      model: "temporarily_unavailable",
      detail: "HTTP 429 quota exceeded",
      expected: "Rate limited",
    },
    {
      overall: "authentication_failed",
      authentication: "invalid",
      model: "not_tested",
      detail: "HTTP 401 authentication failed",
      expected: "Authentication failed",
    },
  ])("shows $overall precisely without claiming the connection failed", async ({
    overall,
    authentication,
    model,
    detail,
    expected,
  }) => {
    const mock = client();
    mock.testModelControlProfile.mockResolvedValue({
      model_profile_uuid: "profile-1",
      health: {
        status: "error",
        provider_type: "openai_compatible_llm",
        model_name: "example-memory-model",
        latency_ms: 12,
        local_only_safe: false,
        dns_or_host_reachable: "reachable",
        tcp_or_http_reachable: "reachable",
        authentication_status: authentication,
        model_status: model,
        chat_completion_status: "not_tested",
        structured_output_status: "not_tested",
        role_compatibility_status: "temporarily_unavailable",
        overall_status: overall,
        retryable: overall === "rate_limited",
        quota_or_rate_limited: overall === "rate_limited",
        detail_sanitized: detail,
        recommended_action: expected,
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
      expect(element.shadowRoot?.textContent).toContain(expected);
    });
    expect(element.shadowRoot?.textContent?.toLowerCase()).not.toContain("connection failed");
    expect(mock.setModelControlDefault).not.toHaveBeenCalled();
  });

  it("uses one idempotent profile identity and suppresses duplicate submits", async () => {
    const mock = client();
    let resolveCreate: ((value: { model_profile_uuid: string; secret_configured: boolean }) => void)
      | undefined;
    mock.createModelControlProfile.mockImplementation(() => new Promise((resolve) => {
      resolveCreate = resolve;
    }));
    const root = document.createElement("div");
    document.body.append(root);
    const element = mountMemoryNodeSetup(root, mock);
    await vi.waitFor(() => {
      expect(element.shadowRoot?.textContent).toContain("Local fallback available");
    });
    const form = element.shadowRoot?.querySelector("form") as HTMLFormElement;

    form.requestSubmit();
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    expect(mock.createModelControlProfile).toHaveBeenCalledTimes(1);
    expect(mock.createModelControlProfile).toHaveBeenCalledWith(expect.objectContaining({
      setup_idempotency_key: "memorist-setup-v1:memory_extraction:deterministic",
    }));
    resolveCreate?.({ model_profile_uuid: "profile-1", secret_configured: false });
  });
});
