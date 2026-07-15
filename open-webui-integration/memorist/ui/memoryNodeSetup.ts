import {
  MemoristClient,
  type MemoryNodeSetupStatus,
  type ModelControlProfileCreate,
  type ModelControlProfileTestResponse,
} from "./memoristClient";
import type { MemoristModelRole, ModelControlProfile } from "./modelControl";

export const MEMORIST_MEMORY_NODE_SETUP_ROUTE = "/settings/memorist/memory-setup";

type SetupClient = Pick<
  MemoristClient,
  | "memoryNodeSetupStatus"
  | "createModelControlProfile"
  | "testModelControlProfile"
  | "setModelControlDefault"
>;

type SetupRole = "memory_extraction" | "high_confidence_extraction";
type ProviderMode = "deterministic" | "openai_compatible";

const ROLE_LABELS: Record<SetupRole, string> = {
  memory_extraction: "Memory extraction",
  high_confidence_extraction: "High-confidence extraction",
};

const STYLES = `
  :host { display:block; color:var(--memorist-text,inherit); font:400 14px/1.45 system-ui,sans-serif; }
  * { box-sizing:border-box; }
  .shell { max-width:880px; margin:auto; padding:clamp(1rem,3vw,2rem); }
  header, .panel { border:1px solid color-mix(in srgb,currentColor 14%,transparent); border-radius:16px; padding:1rem; }
  header { background:color-mix(in srgb,var(--memorist-accent,#6d5bd0) 8%,transparent); margin-bottom:1rem; }
  h1,h2 { margin:.1rem 0 .5rem; line-height:1.2; }
  p { margin:.35rem 0; }
  .status { display:flex; gap:.5rem; align-items:center; flex-wrap:wrap; }
  .badge { display:inline-flex; border-radius:999px; padding:.2rem .55rem; background:color-mix(in srgb,currentColor 8%,transparent); font-size:.82rem; }
  .roles { display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:.6rem; margin:1rem 0; }
  .role { border:1px solid color-mix(in srgb,currentColor 12%,transparent); border-radius:12px; padding:.7rem; }
  form { display:grid; gap:.75rem; }
  .two { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:.75rem; }
  label { display:grid; gap:.25rem; font-weight:600; }
  input,select,button { min-height:2.55rem; border:1px solid color-mix(in srgb,currentColor 22%,transparent); border-radius:10px; padding:.55rem .7rem; background:Canvas; color:inherit; font:inherit; }
  input[type=checkbox] { min-height:auto; width:1rem; height:1rem; }
  .check { display:flex; align-items:flex-start; gap:.5rem; font-weight:500; }
  button { cursor:pointer; background:var(--memorist-accent,#6d5bd0); color:white; font-weight:700; }
  button:disabled { opacity:.55; cursor:not-allowed; }
  .secondary { background:transparent; color:inherit; }
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
  private role: SetupRole = "memory_extraction";
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
      this.message = "Memory setup status could not be loaded.";
      this.messageKind = "error";
    } finally {
      this.loading = false;
      this.render();
    }
  }

  private async save(event: Event): Promise<void> {
    event.preventDefault();
    if (this.saving) return;
    const form = new FormData(event.target as HTMLFormElement);
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

    const deterministicModel = this.role === "memory_extraction"
      ? "deterministic_extraction"
      : "deterministic_high_confidence";
    const payload: ModelControlProfileCreate = this.providerMode === "deterministic"
      ? {
          profile_name: `${ROLE_LABELS[this.role]} — local deterministic`,
          role: this.role,
          provider_type: "deterministic",
          provider_name: "Local deterministic",
          model_name: deterministicModel,
          endpoint_url: null,
          endpoint_is_local: true,
          secret_strategy: "none",
          secret_env_var_name: null,
          is_enabled: true,
          privacy_acknowledged: true,
        }
      : {
          profile_name: `${ROLE_LABELS[this.role]} — ${providerName || "OpenAI-compatible"}`,
          role: this.role,
          provider_type: "openai_compatible_llm",
          provider_name: providerName || "Custom OpenAI-compatible endpoint",
          model_name: modelName,
          endpoint_url: endpoint,
          endpoint_is_local: endpointLocal,
          supports_json_mode: form.get("supports_json_mode") === "on",
          supports_structured_output: form.get("supports_structured_output") === "on",
          secret_strategy: envName ? "env_var" : "none",
          secret_env_var_name: envName || null,
          is_enabled: true,
          privacy_acknowledged: endpointLocal || privacyAcknowledged,
        };

    this.saving = true;
    this.show("Saving and testing the processing node…", "notice");
    try {
      const profile = await this.client.createModelControlProfile(payload);
      const tested = await this.client.testModelControlProfile(
        profile.model_profile_uuid,
        { timeout_ms: 3000 },
      );
      if (tested.health.status !== "ok") {
        this.show(
          `Profile saved, but the connection test failed: ${safeMessage(tested)}`,
          "error",
        );
        return;
      }
      await this.client.setModelControlDefault({
        role: this.role,
        model_profile_uuid: profile.model_profile_uuid,
      });
      (event.target as HTMLFormElement).reset();
      this.providerMode = "deterministic";
      this.show(
        `${ROLE_LABELS[this.role]} is configured. Secret values were not stored or returned.`,
        "success",
      );
      this.status = await this.client.memoryNodeSetupStatus();
    } catch (error) {
      this.show(safeError(error), "error");
    } finally {
      this.saving = false;
      this.render();
    }
  }

  private show(message: string, kind: "notice" | "error" | "success"): void {
    this.message = message;
    this.messageKind = kind;
    this.render();
  }

  private render(): void {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    const status = this.status;
    const roleCards = status?.roles
      .filter((item) => [
        "memory_extraction",
        "high_confidence_extraction",
        "embedding",
        "privacy_sensitivity",
        "import_reconstruction",
      ].includes(item.role))
      .map((item) => `
        <article class="role">
          <strong>${escapeHtml(item.title)}</strong>
          <p>${item.configured ? "Configured profile" : item.available ? "Local fallback ready" : "Not configured"}</p>
          <span class="badge">${escapeHtml(item.provider_name || "Unavailable")} · ${escapeHtml(item.model_name || "—")}</span>
        </article>`)
      .join("") ?? "";

    const remote = this.providerMode === "openai_compatible";
    this.shadowRoot!.innerHTML = `
      <style>${STYLES}</style>
      <main class="shell" data-route="${MEMORIST_MEMORY_NODE_SETUP_ROUTE}">
        <header>
          <p class="hint">Memorist first-run setup</p>
          <h1>Memory processing nodes</h1>
          <p>Choose how Memorist processes captured conversation text. The main chat model remains controlled by Open WebUI.</p>
          <div class="status">
            <span class="badge">${this.loading ? "Checking setup…" : status?.ready_for_memory_processing ? "Ready" : "Setup required"}</span>
            ${status?.local_fallback_available ? '<span class="badge">Local fallback available</span>' : ""}
            ${status ? `<span class="badge">${escapeHtml(status.runtime_profile)} mode</span>` : ""}
          </div>
        </header>
        <section class="roles" aria-label="Memory processing roles">${roleCards}</section>
        <section class="panel">
          <h2>Configure a role</h2>
          <form>
            <div class="two">
              <label>Memory role
                <select name="role">
                  ${(["memory_extraction", "high_confidence_extraction"] as SetupRole[]).map((role) =>
                    `<option value="${role}" ${this.role === role ? "selected" : ""}>${ROLE_LABELS[role]}</option>`
                  ).join("")}
                </select>
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
                <label>Provider name
                  <input name="provider_name" placeholder="Custom OpenAI-compatible endpoint">
                </label>
                <label>Model name
                  <input name="model_name" autocomplete="off" placeholder="Your provider model ID">
                </label>
              </div>
              <label>Endpoint URL
                <input name="endpoint_url" type="url" autocomplete="off" placeholder="https://provider.example/v1">
              </label>
              <label>API-key environment variable name
                <input name="secret_env_var_name" autocomplete="off" placeholder="MEMORIST_MEMORY_EXTRACTION_API_KEY">
              </label>
              <p class="hint">Put the API key in the Memorist backend environment and enter only its variable name here. Memorist never stores or returns the key value.</p>
              <div class="two">
                <label class="check"><input type="checkbox" name="endpoint_is_local"> This endpoint is local/private</label>
                <label class="check"><input type="checkbox" name="supports_json_mode" checked> Supports JSON mode</label>
                <label class="check"><input type="checkbox" name="supports_structured_output"> Supports structured output</label>
              </div>
              <label class="check"><input type="checkbox" name="privacy_acknowledged"> I understand remote processing sends role-specific conversation content to this provider.</label>
            ` : `
              <div class="notice">
                <strong>Local deterministic mode</strong>
                <p>No provider account or API key is required. It is private and reliable, but remote structured models may produce higher-quality extraction.</p>
              </div>
            `}
            <button type="submit" ${this.saving || this.loading ? "disabled" : ""}>${this.saving ? "Saving and testing…" : "Save, test, and use for this role"}</button>
          </form>
          ${this.message ? `<div role="status" class="notice ${this.messageKind}">${escapeHtml(this.message)}</div>` : ""}
          <p class="hint">Memory Off remains authoritative: configured providers are not called for disabled chat turns.</p>
        </section>
      </main>`;

    this.shadowRoot!.querySelector('[name="role"]')?.addEventListener("change", (event) => {
      this.role = (event.target as HTMLSelectElement).value as SetupRole;
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

if (
  typeof customElements !== "undefined"
  && !customElements.get("memorist-memory-node-setup")
) {
  customElements.define("memorist-memory-node-setup", MemoristMemoryNodeSetup);
}

export function mountMemoryNodeSetup(
  container: HTMLElement,
  client?: SetupClient,
): MemoristMemoryNodeSetup {
  const element = document.createElement(
    "memorist-memory-node-setup",
  ) as MemoristMemoryNodeSetup;
  if (client) element.client = client;
  container.append(element);
  return element;
}

function text(value: FormDataEntryValue | null): string {
  return String(value || "").trim();
}

function safeMessage(response: ModelControlProfileTestResponse): string {
  return scrub(response.health.detail || response.health.status || "Connection failed");
}

function safeError(error: unknown): string {
  return scrub(error instanceof Error ? error.message : "Memory setup request failed.");
}

function scrub(value: string): string {
  return value
    .replace(/bearer\s+[^\s,;]+/gi, "Bearer [redacted]")
    .replace(/(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+/gi, "$1=[redacted]")
    .slice(0, 500);
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>'"]/g, (char) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char] || char
  ));
}
