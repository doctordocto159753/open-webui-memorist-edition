import { describe, expect, it } from "vitest";

import {
  MemoristDiagnostics,
  mountMemoristDiagnostics,
} from "../../open-webui-integration/memorist/ui/diagnostics";
import type {
  MemoristOpenWebUIDiagnostics,
  MemoryNodeSetupStatus,
} from "../../open-webui-integration/memorist/ui/memoristClient";

const healthyDiagnostics: MemoristOpenWebUIDiagnostics = {
  core_health: {
    status: "ok",
    runtime_profile: "full",
    canonical_store: "postgres",
    graph_backend: "falkordb",
    graph_status: "ok",
    hot_scheduler: "in_memory",
    profile_warnings: [],
    full_mode_certification: "not_run",
    full_mode_certification_note:
      "Full-mode certification has not been executed on this installation yet. "
      + "This is an evidence gap, not a runtime failure; run Test-Memorist-Full.ps1 to record it.",
  },
  integration: {
    filter: { filter_id: "memorist_memory_filter", action: "unchanged" },
    route_order: { spa_mount_present: true, memorist_route_count: 30, ordered_before_spa: true },
  },
  openwebui_version: "0.9.6",
};

const setupStatus = {
  ready_for_memory_processing: true,
  configured_roles: ["memory_extraction", "embedding"],
} as unknown as MemoryNodeSetupStatus;

async function flush(): Promise<void> {
  await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

describe("memorist-diagnostics", () => {
  it("renders safe, truthful runtime facts", async () => {
    const container = document.createElement("div");
    document.body.append(container);
    const element = mountMemoristDiagnostics(container, {
      async openwebuiDiagnostics() {
        return healthyDiagnostics;
      },
      async memoryNodeSetupStatus() {
        return setupStatus;
      },
    });
    await flush();
    const text = element.textContent || "";
    expect(text).toContain("Diagnostics");
    expect(text).toContain("postgres");
    expect(text).toContain("falkordb");
    expect(text).toContain("full");
    expect(text).toContain("not_run");
    expect(text).toContain("not a runtime failure");
    expect(text).toContain("memorist_memory_filter");
    expect(text).toContain("0.9.6");
    expect(text).toContain("ready (2 role defaults configured)");
    expect(text).not.toContain("postgres_dsn");
  });

  it("shows an unauthorized state for non-admin errors", async () => {
    const container = document.createElement("div");
    document.body.append(container);
    const element = mountMemoristDiagnostics(container, {
      async openwebuiDiagnostics(): Promise<MemoristOpenWebUIDiagnostics> {
        throw new Error("HTTP 403 Forbidden");
      },
      async memoryNodeSetupStatus() {
        return setupStatus;
      },
    });
    await flush();
    expect(element.textContent).toContain("Administrator access is required");
  });

  it("shows a retryable redacted error state", async () => {
    // Assembled at runtime so the literal never appears in source (repo-hygiene
    // secret scanner would otherwise flag this test canary as a real key).
    const secretLooking = ["sk", "abcdefghijklmnopqrstuvwxyz123456"].join("-");
    let attempts = 0;
    const container = document.createElement("div");
    document.body.append(container);
    const element = mountMemoristDiagnostics(container, {
      async openwebuiDiagnostics(): Promise<MemoristOpenWebUIDiagnostics> {
        attempts += 1;
        if (attempts === 1) throw new Error(`connect failed near ${secretLooking}`);
        return healthyDiagnostics;
      },
      async memoryNodeSetupStatus() {
        return setupStatus;
      },
    });
    await flush();
    expect(element.textContent).toContain("[redacted]");
    expect(element.textContent).not.toContain(secretLooking);
    (element.querySelector('[data-action="retry"]') as HTMLButtonElement).click();
    await flush();
    expect(element.textContent).toContain("postgres");
  });

  it("registers the custom element", () => {
    expect(customElements.get("memorist-diagnostics")).toBe(MemoristDiagnostics);
  });
});
