import {
  MemoristClient,
  type MemoryNodeSetupRole,
  type MemoryNodeSetupStatus,
  type ModelControlProfileCreate,
  type ModelControlProfileTestResponse,
} from "./memoristClient";

export const MEMORIST_MEMORY_NODE_SETUP_ROUTE = "/settings/memorist/memory-setup";

type SetupClient = Pick<
  MemoristClient,
  | "memoryNodeSetupStatus"
  | "createModelControlProfile"
  | "testModelControlProfile"
  | "setModelControlDefault"
  | "modelControlEffective"
>;

type SetupRole =
  | "preflight"
  | "memory_extraction"
  | "high_confidence_extraction"
  | "privacy_sensitivity"
  | "embedding"
  | "block_compaction"
  | "import_reconstruction";
type ProviderMode = "deterministic" | "openai_compatible";

const ROLE_LABELS: Record<SetupRole, string> = {
  preflight: "Pre-flight",
  memory_extraction: "Memory extraction",
  high_confidence_extraction: "High-confidence extraction",
  privacy_sensitivity: "Privacy sensitivity",
  embedding: "Embedding",
  block_compaction: "Block compaction",
  import_reconstruction: "Import reconstruction",
};
const SETUP_ROLES = Object.keys(ROLE_LABELS) as SetupRole[];

const STYLES = `
  :host { display:block; color:var(--memorist-text,inherit); font:400 14px/1.45 system-ui,sans-serif; }
  * { box-sizing:border-box; }
  .shell { max-width:980px; margin:auto; padding:clamp(1rem,3vw,2rem); }
  header,.panel { border:1px solid color-mix(in srgb,currentColor 14%,transparent); border-radius:16px; padding:1rem; }
  header { background:color-mix(in srgb,var(--memorist-accent,#6d5bd0) 8%,transparent); margin-bottom:1rem; }
  h1,h2 { margin:.1rem 0 .5rem; line-height:1.2; }
  p { margin:.35rem 0; }
  .status { display:flex; gap:.5rem; align-items:center; flex-wrap:wrap; }
  .badge { display:inline-flex; border-radius:999px; padding:.2rem .55rem; background:color-mix(in srgb,currentColor 8%,transparent); font-size:.82rem; }
  .roles { display:grid; grid-template-columns:repeat(auto-fit,minmax(250px,1fr)); gap:.6rem; margin:1rem 0; }
  .role { border:1px solid color-mix(in srgb,currentColor 12%,transparent); border-radius:12px; padding:.7rem; overflow-wrap:anywhere; }
  form { display:grid; gap:.75rem; }
  .two { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:.75rem; }
  label { display:grid; gap:.25rem; font-weight:600; }
  input,select,button { min-height:2.55rem; border:1px solid color-mix(in srgb,currentColor 22%,transparent); border-radius:10px; padding:.55rem .7rem; background:Canvas; color:inherit; font:inherit; }
  input[type=checkbox] { min-height:auto; width:1rem; height:1rem; }
  .check { display:flex; align-items:flex-start; gap:.5rem; font-weight:500; }
  button { cursor:pointer; background:var(--memorist-accent,#6d5bd0); color:white; font-weight:700; }
  button:disabled { opacity:.55; cursor:not-allowed; }
  .hint { font-size:.84rem; opacity:.78; }
  .notice { border-left:3px solid var(--memorist-accent,#6d5bd0); padding:.55rem .7rem; background:color-mix(in srgb,currentColor 5%,transparent); }
  .error { color:#a33; border-left-color:#a33; }
  .success { color:#176b3a; border-left-color:#176b3a; }
  @media(max-width:36rem){ .two{grid-template-columns:1fr} .shell{padding:.65rem} header,.panel{border-radius:12px} }
`;

export class MemoristMemoryNodeSetup extends HTMLElement {
  client: SetupClient = new MemoristClient();
  private status: MemoryNodeSetupStatus | null = null;
  private loading = true;
  private saving = false;
  private message = "";
  private messageKind: "notice" | "error" | "success" = "notice";
  private selectedRole: SetupRole = "memory_extraction";
  private providerMode: ProviderMode = "deterministic";

  connectedCallback(): void {
    this.render();
    void this.refresh();
  }

  async refresh(): Promise<void> {
    this.loading = true;
    this.render();
    try {
      this.status = await this.client.memoryNodeSetupStatus();
      this.message = "";
    } catch {
      this.show("Memory setup status could not be loaded.", "error");
    } finally {
      this.loading = false;
      this.render();
    }
  }

  private async save(event: Event): Promise<void> {
    event.preventDefault();
    if (this.saving) return;
    const formElement = event.currentTarget as HTMLFormElement;
    const form = new FormData(formElement);
    const endpoint = text(form.get("endpoint_url"));
    const modelName = text(form.get("model_name"));
    const envName = text(form.get("secret_env_var_name"));
    const providerName = text(form.get("provider_name"));
    const endpointLocal = form.get("endpoint_is_local") === "on";
    const privacyAcknowledged = form.get("privacy_acknowledged") === "on";

    if (this.providerMode === "openai_compatible") {
      if (!endpoint || !modelName) {
        this.show("Endpoint URL and model name are required.", "error");
        return;
      }
      if (!endpointLocal && !envName) {
        this.show("Remote providers require an API-key environment variable name.", "error");
        return;
      }
      if (!endpointLocal && !privacyAcknowledged) {
        this.show("Review and acknowledge remote-processing privacy before continuing.", "error");
        return;
      }
      if (envName && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(envName)) {
        this.show("Use a valid environment variable name, not an API key.", "error");
        return;
      }
    }

    const identity = `memorist-setup-v1:${this.selectedRole}:${this.providerMode}`;
    const payload = this.profilePayload(form, {
      endpoint,
      modelName,
      envName,
      providerName,
      endpointLocal,
      privacyAcknowledged,
      identity,
    });

    this.saving = true;
    this.show("Saving and testing the processing node…", "notice");
    try {
      const profile = await this.client.createModelControlProfile(payload);
      const tested = await this.client.testModelControlProfile(profile.model_profile_uuid, {
        idempotency_key: `${identity}:test`,
      });
      if (!roleCompatible(tested)) {
        this.show(`Profile saved for editing. ${safeMessage(tested)}`, "error");
        return;
      }
      await this.client.setModelControlDefault({
        role: this.selectedRole,
        model_profile_uuid: profile.model_profile_uuid,
      });
      const [status, effective] = await Promise.all([
        this.client.memoryNodeSetupStatus(),
        this.client.modelControlEffective(),
      ]);
      this.status = status;
      const active = effective.items.find((item) => item.role === this.selectedRole);
      if (active?.model_profile_uuid !== profile.model_profile_uuid) {
        this.show(
          "The role test passed, but the server did not confirm this profile as effective.",
          "error",
        );
        return;
      }
      formElement.reset();
      this.providerMode = "deterministic";
      this.show(
        `${ROLE_LABELS[this.selectedRole]} is configured and active. Secret values were not stored or returned.`,
        "success",
      );
    } catch (error) {
      this.show(safeError(error), "error");
    } finally {
      this.saving = false;
      this.render();
    }
  }

  private profilePayload(
    form: FormData,
    values: {
      endpoint: string;
      modelName: string;
      envName: string;
      providerName: string;
      endpointLocal: boolean;
      privacyAcknowledged: boolean;
      identity: string;
    },
  ): ModelControlProfileCreate {
    if (this.providerMode === "deterministic") {
      return {
        profile_name: `${ROLE_LABELS[this.selectedRole]} — local deterministic`,
        role: this.selectedRole,
        provider_type: "deterministic",
        provider_name: "Local deterministic",
        model_name: `deterministic_${this.selectedRole}`,
        endpoint_url: null,
        endpoint_is_local: true,
        secret_strategy: "none",
        secret_env_var_name: null,
        is_enabled: true,
        privacy_acknowledged: true,
        setup_idempotency_key: values.identity,
      };
    }
    return {
      profile_name: `${ROLE_LABELS[this.selectedRole]} — ${values.providerName || "OpenAI-compatible"}`,
      role: this.selectedRole,
      provider_type: this.selectedRole === "embedding"
        ? "openai_compatible_embedding"
        : "openai_compatible_llm",
      provider_name: values.providerName || "Custom OpenAI-compatible endpoint",
      model_name: values.modelName,
      endpoint_url: values.endpoint,
      endpoint_is_local: values.endpointLocal,
      supports_json_mode: form.get("supports_json_mode") === "on",
      supports_structured_output: form.get("supports_structured_output") === "on",
      supports_embeddings: this.selectedRole === "embedding",
      secret_strategy: values.envName ? "env_var" : "none",
      secret_env_var_name: values.envName || null,
      is_enabled: true,
      privacy_acknowledged: values.endpointLocal || values.privacyAcknowledged,
      setup_idempotency_key: values.identity,
    };
  }

  private show(message: string, kind: "notice" | "error" | "success"): void {
    this.message = message;
    this.messageKind = kind;
    this.render();
  }

  private render(): void {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    const roleCards = this.status?.roles.map((item) => `
      <article class="role">
        <strong>${escapeHtml(item.title)}</strong>
        <p>${escapeHtml(roleState(item))}</p>
        <span class="badge">${escapeHtml(item.provider_name || "Unavailable")} · ${escapeHtml(item.model_name || "—")}</span>
        <p class="hint">Effective: ${escapeHtml(item.effective_role)} · ${escapeHtml(item.source)} · ${item.endpoint_is_local ? "local" : "remote"}</p>
        <p class="hint">Secret: ${item.secret_configured ? item.secret_available ? "configured" : "reference missing" : "not required"} · Privacy: ${item.privacy_acknowledged ? "acknowledged" : "required"}</p>
        <p class="hint">Health: ${escapeHtml(item.last_health?.status || "never tested")} · Runtime: ${item.runtime_wired ? item.last_runtime_use ? `used ${item.last_runtime_use.count} time(s)` : "wired, never exercised" : "not wired"}</p>
        ${item.fallback_reason ? `<p class="hint">Fallback: ${escapeHtml(item.fallback_reason)}</p>` : ""}
      </article>`).join("") ?? "";
    const remote = this.providerMode === "openai_compatible";

    this.shadowRoot!.innerHTML = `
      <style>${STYLES}</style>
      <main class="shell" data-route="${MEMORIST_MEMORY_NODE_SETUP_ROUTE}">
        <header>
          <p class="hint">Memorist first-run setup</p>
          <h1>Memory processing nodes</h1>
          <p>Configure each Memorist stage independently. The main chat model remains controlled by Open WebUI.</p>
          <div class="status">
            <span class="badge">${this.loading ? "Checking setup…" : this.status?.ready_for_memory_processing ? "Ready" : "Setup required"}</span>
            ${this.status?.local_fallback_available ? '<span class="badge">Local fallback available</span>' : ""}
            ${this.status ? `<span class="badge">${escapeHtml(this.status.runtime_profile)} mode</span>` : ""}
          </div>
        </header>
        <section class="roles" aria-label="Memory processing roles">${roleCards}</section>
        <section class="panel">
          <h2>Configure a role</h2>
          <form>
            <div class="two">
              <label>Memory role
                <select name="role">${SETUP_ROLES.map((role) =>
                  `<option value="${role}" ${this.selectedRole === role ? "selected" : ""}>${ROLE_LABELS[role]}</option>`
                ).join("")}</select>
              </label>
              <label>Processing mode
                <select name="provider_mode">
                  <option value="deterministic" ${remote ? "" : "selected"}>Local deterministic — no API key</option>
                  <option value="openai_compatible" ${remote ? "selected" : ""}>OpenAI-compatible / custom endpoint</option>
                </select>
              </label>
            </div>
            ${remote ? `
              <div class="two">
                <label>Provider name <input name="provider_name" placeholder="Custom OpenAI-compatible endpoint"></label>
                <label>Model name <input name="model_name" autocomplete="off" placeholder="Provider model ID"></label>
              </div>
              <label>Endpoint URL <input name="endpoint_url" type="url" autocomplete="off" placeholder="https://provider.example/v1"></label>
              <label>API-key environment variable name <input name="secret_env_var_name" autocomplete="off" placeholder="MEMORIST_PROCESSING_API_KEY"></label>
              <p class="hint">Put the API key in the Memorist backend environment and enter only its variable name here. Memorist never stores or returns the key value.</p>
              <div class="two">
                <label class="check"><input type="checkbox" name="endpoint_is_local"> This endpoint is local/private</label>
                <label class="check"><input type="checkbox" name="supports_json_mode" checked> Supports JSON mode</label>
                <label class="check"><input type="checkbox" name="supports_structured_output"> Supports structured output</label>
              </div>
              <label class="check"><input type="checkbox" name="privacy_acknowledged"> I understand remote processing sends role-specific conversation content to this provider.</label>
            ` : `
              <div class="notice"><strong>Local deterministic mode</strong><p>No provider account or API key is required.</p></div>
            `}
            <button type="submit" ${this.saving || this.loading ? "disabled" : ""}>${this.saving ? "Saving and testing…" : "Save, test, and use for this role"}</button>
          </form>
          ${this.message ? `<div role="status" class="notice ${this.messageKind}">${escapeHtml(this.message)}</div>` : ""}
          <p class="hint">Memory Off and Private Mode remain authoritative: configured providers are not called for disabled turns.</p>
        </section>
      </main>`;

    this.shadowRoot!.querySelector('[name="role"]')?.addEventListener("change", (event) => {
      this.selectedRole = (event.target as HTMLSelectElement).value as SetupRole;
    });
    this.shadowRoot!.querySelector('[name="provider_mode"]')?.addEventListener("change", (event) => {
      this.providerMode = (event.target as HTMLSelectElement).value as ProviderMode;
      this.render();
    });
    this.shadowRoot!.querySelector("form")?.addEventListener("submit", (event) => {
      void this.save(event);
    });
  }
}

if (typeof customElements !== "undefined" && !customElements.get("memorist-memory-node-setup")) {
  customElements.define("memorist-memory-node-setup", MemoristMemoryNodeSetup);
}

export function mountMemoryNodeSetup(
  container: HTMLElement,
  client?: SetupClient,
): MemoristMemoryNodeSetup {
  const element = document.createElement("memorist-memory-node-setup") as MemoristMemoryNodeSetup;
  if (client) element.client = client;
  container.append(element);
  return element;
}

function text(value: FormDataEntryValue | null): string {
  return String(value || "").trim();
}

function roleCompatible(response: ModelControlProfileTestResponse): boolean {
  return response.health.overall_status === "ok"
    && response.health.role_compatibility_status === "compatible";
}

function safeMessage(response: ModelControlProfileTestResponse): string {
  const health = response.health;
  const summary = [
    `Connection: ${health.tcp_or_http_reachable}`,
    `Authentication: ${health.authentication_status}`,
    `Model: ${health.model_status}`,
    `Structured output: ${health.structured_output_status}`,
    `Role compatibility: ${health.role_compatibility_status}`,
    `Overall: ${health.overall_status}`,
  ];
  if (health.detail_sanitized) summary.push(health.detail_sanitized);
  if (health.recommended_action) summary.push(`Recommended action: ${health.recommended_action}`);
  return scrub(summary.join(". "));
}

function roleState(item: MemoryNodeSetupRole): string {
  if (!item.runtime_wired) return "Configured but not wired";
  if (!item.capability_compatible) return "Configured but incompatible";
  if (item.inheritance_source) return `Inherited from ${item.inheritance_source}`;
  if (item.source === "built_in_fallback") return "Built-in deterministic fallback";
  if (item.configured && item.available) return "Configured and active";
  if (!item.available) return "Disabled";
  return "Configured but not default";
}

function safeError(error: unknown): string {
  return scrub(error instanceof Error ? error.message : "Memory setup request failed.");
}

function scrub(value: string): string {
  return value
    .replace(/bearer\s+[^\s,;]+/gi, "Bearer [redacted]")
    .replace(/(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+/gi, "$1=[redacted]")
    .slice(0, 1000);
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>'"]/g, (char) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char] || char
  ));
}
