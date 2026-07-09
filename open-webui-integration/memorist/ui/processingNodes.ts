import { MemoristClient, type ModelControlDefault, type ModelControlHealthEvent, type ModelControlRoleDefaultSet, type PrivacyAcknowledgementRequest } from "./memoristClient";
import { MEMORIST_MODEL_ROLES, PROVIDER_TYPES, type MemoristModelRole, type ModelControlProfile, roleHelpText, rolePrivacyBadge } from "./modelControl";

export const MEMORIST_PROCESSING_NODES_ROUTE = "/settings/memorist/processing-nodes";

type HealthByProfile = Record<string, ModelControlHealthEvent | undefined>;

export const MEMORIST_PROCESSING_NODE_SELECTABLE_ROLES = MEMORIST_MODEL_ROLES.filter(
  (role) => role !== "main_chat_observed",
);

const MAIN_CHAT_OBSERVED_NOTE = "Selected in Open WebUI; Memorist observes metadata only.";
const PRIVACY_ACK_REQUIRED_LABEL = "Privacy acknowledgement required";
const PRIVACY_ACK_REQUIRED_ERROR = "Privacy acknowledgement required before assigning this remote profile as a role default.";

function requiresPrivacyAcknowledgement(profile: Pick<ModelControlProfile, "endpoint_is_local" | "privacy_acknowledged_at">): boolean {
  return profile.endpoint_is_local === false && !profile.privacy_acknowledged_at;
}

type ProcessingNodesState = {
  profiles: ModelControlProfile[];
  defaults: ModelControlDefault[];
  health: HealthByProfile;
  loading: boolean;
  saving: boolean;
  error: string | null;
  form: Partial<ModelControlProfile> & { endpoint_url?: string | null; secret_env_var_name?: string | null; secret_strategy?: string };
  editingProfileUuid: string | null;
  testState: Record<string, string>;
};

const EMPTY_FORM: ProcessingNodesState["form"] = {
  profile_name: "",
  role: "memory_extraction",
  provider_type: "deterministic",
  model_name: "deterministic_extraction",
  endpoint_url: "",
  endpoint_is_local: true,
  is_enabled: true,
  secret_strategy: "none",
  secret_env_var_name: "",
  supports_json_mode: false,
  supports_structured_output: false,
  supports_embeddings: false,
  embedding_dimension: null,
};

export class MemoristProcessingNodesSettings extends HTMLElement {
  private readonly client = new MemoristClient(this.getAttribute("memcore-base-url") || "/memcore");
  private state: ProcessingNodesState = {
    profiles: [],
    defaults: [],
    health: {},
    loading: true,
    saving: false,
    error: null,
    form: { ...EMPTY_FORM },
    editingProfileUuid: null,
    testState: {},
  };

  connectedCallback(): void {
    void this.refresh();
  }

  async refresh(): Promise<void> {
    this.setState({ loading: true, error: null });
    try {
      const [profilesResponse, defaultsResponse, healthResponse] = await Promise.all([
        this.client.modelControlProfiles(),
        this.client.modelControlDefaults(),
        this.client.modelControlHealth(),
      ]);
      const latestHealth = (healthResponse.latest_health_events || []).reduce<HealthByProfile>((acc, event) => {
        acc[event.model_profile_uuid] = event;
        return acc;
      }, {});
      this.setState({
        profiles: profilesResponse.items || [],
        defaults: defaultsResponse.items || [],
        health: latestHealth,
        loading: false,
      });
    } catch (error) {
      this.setState({ loading: false, error: errorMessage(error) });
    }
  }

  render(): void {
    const rows = this.state.profiles.map((profile) => this.profileRow(profile)).join("");
    this.innerHTML = `
      <section class="memorist-processing-nodes" data-route="${MEMORIST_PROCESSING_NODES_ROUTE}">
        <header>
          <p class="eyebrow">Memorist admin settings</p>
          <h1>Processing Nodes</h1>
          <p>Configure Memorist model-control profiles for extraction, embedding, preflight checks, and other background processing roles.</p>
        </header>
        ${this.state.error ? `<div role="alert" class="error">${escapeHtml(this.state.error)}</div>` : ""}
        <div class="toolbar">
          <button type="button" data-action="refresh" ${this.state.loading ? "disabled" : ""}>${this.state.loading ? "Loading…" : "Refresh"}</button>
          <button type="button" data-action="new">New profile</button>
        </div>
        <div class="grid-wrap">
          <table>
            <thead><tr>${["Profile", "Role", "Provider", "Endpoint", "Model", "Local/Remote", "Privacy", "Enabled", "Default", "Health", "Actions"].map((heading) => `<th>${heading}</th>`).join("")}</tr></thead>
            <tbody>${rows || `<tr><td colspan="11">No model-control profiles have been configured.</td></tr>`}</tbody>
          </table>
        </div>
        ${this.formHtml()}
      </section>`;
    this.bindEvents();
  }

  private profileRow(profile: ModelControlProfile): string {
    const health = this.state.health[profile.model_profile_uuid];
    const isDefaultFor = this.state.defaults.filter((item) => item.model_profile_uuid === profile.model_profile_uuid).map((item) => item.role);
    const privacy = rolePrivacyBadge(profile);
    return `<tr>
      <td>${escapeHtml(profile.profile_name || profile.model_name)}</td>
      <td><code title="${escapeHtml(roleHelpText(profile.role))}">${escapeHtml(profile.role)}</code></td>
      <td>${escapeHtml(profile.provider_type)}</td>
      <td>${escapeHtml(profile.endpoint_url || "—")}</td>
      <td>${escapeHtml(profile.model_name)}</td>
      <td>${profile.endpoint_is_local ? "Local" : "Remote"}</td>
      <td><span class="badge ${privacy}">${escapeHtml(privacy)}</span></td>
      <td>${profile.is_enabled ? "Enabled" : "Disabled"}</td>
      <td>${isDefaultFor.length ? isDefaultFor.map(escapeHtml).join(", ") : "—"}</td>
      <td>${health ? `${escapeHtml(health.status)}${health.latency_ms == null ? "" : ` (${health.latency_ms} ms)`}` : escapeHtml(this.state.testState[profile.model_profile_uuid] || "Not tested")}</td>
      <td>
        <button type="button" data-action="edit" data-profile="${profile.model_profile_uuid}">Edit</button>
        <button type="button" data-action="test" data-profile="${profile.model_profile_uuid}">Test</button>
        ${profile.endpoint_is_local || profile.privacy_acknowledged_at ? "" : `<button type="button" data-action="ack" data-profile="${profile.model_profile_uuid}">Acknowledge privacy</button>`}
      </td>
    </tr>`;
  }

  private formHtml(): string {
    const form = this.state.form;
    const profileOptions = this.state.profiles.map((profile) => {
      const privacyAckRequired = requiresPrivacyAcknowledgement(profile);
      const label = `${profile.profile_name || profile.model_name} (${profile.role})${privacyAckRequired ? ` — ${PRIVACY_ACK_REQUIRED_LABEL}` : ""}`;
      return `<option value="${profile.model_profile_uuid}" ${privacyAckRequired ? "disabled" : ""}>${escapeHtml(label)}</option>`;
    }).join("");
    return `<form class="profile-form" data-action="save-profile">
      <h2>${this.state.editingProfileUuid ? "Edit profile" : "Create profile"}</h2>
      <label>Name <input name="profile_name" value="${escapeHtml(String(form.profile_name || ""))}"></label>
      <label>Role <select name="role">${MEMORIST_PROCESSING_NODE_SELECTABLE_ROLES.map((role) => `<option value="${role}" ${form.role === role ? "selected" : ""}>${role}</option>`).join("")}</select></label>
      <p class="hint"><strong>Main chat:</strong> ${MAIN_CHAT_OBSERVED_NOTE}</p>
      <label>Provider <select name="provider_type">${PROVIDER_TYPES.map((provider) => `<option value="${provider}" ${form.provider_type === provider ? "selected" : ""}>${provider}</option>`).join("")}</select></label>
      <label>Model <input name="model_name" required value="${escapeHtml(String(form.model_name || ""))}"></label>
      <label>Endpoint URL <input name="endpoint_url" placeholder="http://localhost:11434" value="${escapeHtml(String(form.endpoint_url || ""))}"></label>
      <label><input type="checkbox" name="endpoint_is_local" ${form.endpoint_is_local !== false ? "checked" : ""}> Endpoint is local</label>
      <label><input type="checkbox" name="is_enabled" ${form.is_enabled !== false ? "checked" : ""}> Enabled</label>
      <label><input type="checkbox" name="supports_json_mode" ${form.supports_json_mode ? "checked" : ""}> Supports JSON mode</label>
      <label><input type="checkbox" name="supports_structured_output" ${form.supports_structured_output ? "checked" : ""}> Supports structured output</label>
      <label><input type="checkbox" name="supports_embeddings" ${form.supports_embeddings ? "checked" : ""}> Supports embeddings</label>
      <label>Embedding dimension <input name="embedding_dimension" type="number" min="1" step="1" inputmode="numeric" value="${form.embedding_dimension == null ? "" : escapeHtml(String(form.embedding_dimension))}"></label>
      <label>Secret env var <input name="secret_env_var_name" value="${escapeHtml(String(form.secret_env_var_name || ""))}"></label>
      <div class="actions"><button type="submit" ${this.state.saving ? "disabled" : ""}>${this.state.saving ? "Saving…" : "Save profile"}</button><button type="button" data-action="cancel-edit">Cancel</button></div>
      <h2>Role default</h2>
      <label>Role <select name="default_role">${MEMORIST_PROCESSING_NODE_SELECTABLE_ROLES.map((role) => `<option value="${role}">${role}</option>`).join("")}</select></label>
      <label>Default profile <select name="default_profile_uuid">${profileOptions}</select></label>
      <button type="button" data-action="set-default">Set role default</button>
    </form>`;
  }

  private bindEvents(): void {
    this.querySelector('[data-action="refresh"]')?.addEventListener("click", () => void this.refresh());
    this.querySelector('[data-action="new"]')?.addEventListener("click", () => this.setState({ editingProfileUuid: null, form: { ...EMPTY_FORM }, error: null }));
    this.querySelector('[data-action="cancel-edit"]')?.addEventListener("click", () => this.setState({ editingProfileUuid: null, form: { ...EMPTY_FORM }, error: null }));
    this.querySelectorAll('[data-action="edit"]').forEach((button) => button.addEventListener("click", () => this.editProfile((button as HTMLElement).dataset.profile || "")));
    this.querySelectorAll('[data-action="test"]').forEach((button) => button.addEventListener("click", () => void this.testProfile((button as HTMLElement).dataset.profile || "")));
    this.querySelectorAll('[data-action="ack"]').forEach((button) => button.addEventListener("click", () => void this.acknowledgePrivacy((button as HTMLElement).dataset.profile || "")));
    this.querySelector('[data-action="save-profile"]')?.addEventListener("submit", (event) => void this.saveProfile(event));
    this.querySelector('[data-action="set-default"]')?.addEventListener("click", () => void this.setDefault());
  }

  private editProfile(profileUuid: string): void {
    const profile = this.state.profiles.find((item) => item.model_profile_uuid === profileUuid);
    if (profile) this.setState({ editingProfileUuid: profileUuid, form: { ...profile }, error: null });
  }

  private async saveProfile(event: Event): Promise<void> {
    event.preventDefault();
    const form = new FormData(event.target as HTMLFormElement);
    const payload = {
      profile_name: stringOrNull(form.get("profile_name")),
      role: form.get("role") as MemoristModelRole,
      provider_type: String(form.get("provider_type")),
      model_name: String(form.get("model_name") || "disabled"),
      endpoint_url: stringOrNull(form.get("endpoint_url")),
      endpoint_is_local: form.get("endpoint_is_local") === "on",
      is_enabled: form.get("is_enabled") === "on",
      supports_json_mode: form.get("supports_json_mode") === "on",
      supports_structured_output: form.get("supports_structured_output") === "on",
      supports_embeddings: form.get("supports_embeddings") === "on",
      embedding_dimension: numberOrNull(form.get("embedding_dimension")),
      secret_strategy: stringOrNull(form.get("secret_env_var_name")) ? "env_var" : "none",
      secret_env_var_name: stringOrNull(form.get("secret_env_var_name")),
    };
    this.setState({ saving: true, error: null });
    try {
      if (this.state.editingProfileUuid) await this.client.patchModelControlProfile(this.state.editingProfileUuid, payload);
      else await this.client.createModelControlProfile(payload);
      this.state.form = { ...EMPTY_FORM };
      this.state.editingProfileUuid = null;
      await this.refresh();
    } catch (error) {
      this.setState({ saving: false, error: errorMessage(error) });
    }
  }

  private async setDefault(): Promise<void> {
    const role = (this.querySelector('[name="default_role"]') as HTMLSelectElement | null)?.value as MemoristModelRole | undefined;
    const modelProfileUuid = (this.querySelector('[name="default_profile_uuid"]') as HTMLSelectElement | null)?.value;
    if (!role || !modelProfileUuid) return;
    const profile = this.state.profiles.find((item) => item.model_profile_uuid === modelProfileUuid);
    if (profile && requiresPrivacyAcknowledgement(profile)) {
      this.setState({ error: PRIVACY_ACK_REQUIRED_ERROR });
      return;
    }
    try {
      await this.client.setModelControlDefault({ role, model_profile_uuid: modelProfileUuid } satisfies ModelControlRoleDefaultSet);
      await this.refresh();
    } catch (error) {
      this.setState({ error: errorMessage(error) });
    }
  }

  private async testProfile(profileUuid: string): Promise<void> {
    this.state.testState[profileUuid] = "Testing…";
    this.render();
    try {
      await this.client.testModelControlProfile(profileUuid, { timeout_ms: 1000 });
      await this.refresh();
    } catch (error) {
      this.state.testState[profileUuid] = errorMessage(error);
      this.setState({ error: errorMessage(error) });
    }
  }

  private async acknowledgePrivacy(profileUuid: string): Promise<void> {
    const profile = this.state.profiles.find((item) => item.model_profile_uuid === profileUuid);
    const request: PrivacyAcknowledgementRequest = {
      model_profile_uuid: profileUuid,
      acknowledged_risk_level: String(profile?.privacy_profile?.risk_level || "external"),
      acknowledged_data_sent: profile?.privacy_profile || { remote_endpoint: true },
    };
    try {
      await this.client.acknowledgeModelControlPrivacy(request);
      await this.refresh();
    } catch (error) {
      this.setState({ error: errorMessage(error) });
    }
  }

  private setState(patch: Partial<ProcessingNodesState>): void {
    this.state = { ...this.state, ...patch };
    this.render();
  }
}

if (typeof customElements !== "undefined" && !customElements.get("memorist-processing-nodes-settings")) {
  customElements.define("memorist-processing-nodes-settings", MemoristProcessingNodesSettings);
}

function stringOrNull(value: FormDataEntryValue | null): string | null {
  const text = String(value || "").trim();
  return text.length ? text : null;
}

function numberOrNull(value: FormDataEntryValue | null): number | null {
  const text = String(value || "").trim();
  if (!text.length) return null;
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : null;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char] || char);
}
