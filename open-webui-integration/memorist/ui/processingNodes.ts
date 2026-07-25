import { MemoristClient, type ModelControlDefault, type ModelControlEffectiveRole, type ModelControlHealthEvent, type ModelControlProfileCreate, type ModelControlProfileTestResponse, type ModelControlRoleDefaultSet, type PrivacyAcknowledgementRequest } from "./memoristClient";
import { MEMORIST_MODEL_ROLES, PROVIDER_TYPES, type MemoristModelRole, type ModelControlProfile, roleHelpText, rolePrivacyBadge } from "./modelControl";

export const MEMORIST_PROCESSING_NODES_ROUTE = "/settings/memorist/processing-nodes";

type HealthByProfile = Record<string, ModelControlHealthEvent | undefined>;

export const MEMORIST_PROCESSING_NODE_SELECTABLE_ROLES = MEMORIST_MODEL_ROLES.filter(
  (role) => role !== "main_chat_observed",
);

const MAIN_CHAT_OBSERVED_NOTE = "Selected in Open WebUI; Memorist observes metadata only.";
const PRIVACY_ACK_REQUIRED_LABEL = "Privacy acknowledgement required";
const PRIVACY_ACK_REQUIRED_ERROR = "Privacy acknowledgement required before assigning this remote profile as a role default.";
const DISABLED_PROFILE_DEFAULT_ERROR = "Disabled profiles cannot be assigned as role defaults.";

function isProcessingNodeProfile(profile: Pick<ModelControlProfile, "role">): boolean {
  return profile.role !== "main_chat_observed";
}

function requiresPrivacyAcknowledgement(profile: Pick<ModelControlProfile, "endpoint_is_local" | "privacy_acknowledged_at">): boolean {
  return profile.endpoint_is_local === false && !profile.privacy_acknowledged_at;
}

type ProcessingNodesState = {
  profiles: ModelControlProfile[];
  defaults: ModelControlDefault[];
  health: HealthByProfile;
  effective: ModelControlEffectiveRole[];
  loading: boolean;
  saving: boolean;
  error: string | null;
  form: Partial<ModelControlProfile> & { endpoint_url?: string | null; secret_env_var_name?: string | null; secret_strategy?: string };
  editingProfileUuid: string | null;
  testState: Record<string, string>;
  testResults: Record<string, ModelControlProfileTestResponse | undefined>;
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
    effective: [],
    loading: true,
    saving: false,
    error: null,
    form: { ...EMPTY_FORM },
    editingProfileUuid: null,
    testState: {},
    testResults: {},
  };

  connectedCallback(): void {
    void this.refresh();
  }

  async refresh(): Promise<void> {
    this.setState({ loading: true, error: null });
    try {
      const [profilesResponse, defaultsResponse, healthResponse, effectiveResponse] = await Promise.all([
        this.client.modelControlProfiles(),
        this.client.modelControlDefaults(),
        this.client.modelControlHealth(),
        this.client.modelControlEffective(),
      ]);
      const latestHealth = (healthResponse.latest_health_events || []).reduce<HealthByProfile>((acc, event) => {
        acc[event.model_profile_uuid] = event;
        return acc;
      }, {});
      this.setState({
        profiles: profilesResponse.items || [],
        defaults: defaultsResponse.items || [],
        health: latestHealth,
        effective: effectiveResponse.items || [],
        loading: false,
      });
    } catch (error) {
      this.setState({ loading: false, error: errorMessage(error) });
    }
  }

  render(): void {
    const processingNodeProfiles = this.state.profiles.filter(isProcessingNodeProfile);
    const rows = processingNodeProfiles.map((profile) => this.profileRow(profile)).join("");
    const effectiveCards = this.state.effective.map((item) => `
      <article class="effective-role">
        <strong>${escapeHtml(item.role)}</strong>
        <span>${escapeHtml(item.provider_type)} · ${escapeHtml(item.model_name)}</span>
        <small>${escapeHtml(effectiveState(item))}; ${item.endpoint_is_local ? "local" : "remote"}${item.fallback_reason ? `; fallback: ${escapeHtml(item.fallback_reason)}` : ""}</small>
      </article>`).join("");
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
        <section class="effective-grid" aria-label="Effective processing roles">${effectiveCards}</section>
        <div class="grid-wrap">
          <table>
            <thead><tr>${["Profile", "Role", "Provider", "Endpoint", "Model", "Local/Remote", "Privacy", "Secret", "Enabled", "Default", "Health", "Actions"].map((heading) => `<th>${heading}</th>`).join("")}</tr></thead>
            <tbody>${rows || `<tr><td colspan="12">No Memorist processing-node profiles have been configured.</td></tr>`}</tbody>
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
      <td>${secretState(profile)}</td>
      <td>${profile.is_enabled ? "Enabled" : "Disabled"}</td>
      <td>${isDefaultFor.length ? isDefaultFor.map(escapeHtml).join(", ") : "—"}</td>
      <td>${this.healthCell(profile, health)}</td>
      <td>
        <button type="button" data-action="edit" data-profile="${profile.model_profile_uuid}">Edit</button>
        <button type="button" data-action="test" data-profile="${profile.model_profile_uuid}">Test</button>
        ${isDefaultFor.map((role) => `<button type="button" data-action="remove-default" data-role="${escapeHtml(role)}">Remove ${escapeHtml(role)} default</button>`).join("")}
        ${profile.endpoint_is_local || profile.privacy_acknowledged_at ? "" : `<button type="button" data-action="ack" data-profile="${profile.model_profile_uuid}">Acknowledge privacy</button>`}
      </td>
    </tr>`;
  }

  private healthCell(
    profile: ModelControlProfile,
    health: ModelControlHealthEvent | undefined,
  ): string {
    const result = this.state.testResults[profile.model_profile_uuid]?.health;
    if (result) {
      return [
        `Connection: ${result.tcp_or_http_reachable}`,
        `Authentication: ${result.authentication_status}`,
        `Model: ${result.model_status}`,
        `Role: ${result.role_compatibility_status}`,
        `Overall: ${result.overall_status}`,
        result.quota_or_rate_limited ? "Rate/quota limited" : "",
        result.detail_sanitized || "",
        result.recommended_action || "",
      ].filter(Boolean).map((value) => escapeHtml(value)).join("<br>");
    }
    return health
      ? `${escapeHtml(health.status)}${health.latency_ms == null ? "" : ` (${health.latency_ms} ms)`}${health.detail_sanitized ? `<br>${escapeHtml(health.detail_sanitized)}` : ""}`
      : escapeHtml(this.state.testState[profile.model_profile_uuid] || "Not tested");
  }

  private formHtml(): string {
    const form = this.state.form;
    const profileOptions = this.state.profiles.filter(isProcessingNodeProfile).map((profile) => {
      const privacyAckRequired = requiresPrivacyAcknowledgement(profile);
      const disabledProfile = profile.is_enabled === false;
      const disabledReason = privacyAckRequired ? PRIVACY_ACK_REQUIRED_LABEL : disabledProfile ? "Profile disabled" : "";
      const label = `${profile.profile_name || profile.model_name} (${profile.role})${disabledReason ? ` — ${disabledReason}` : ""}`;
      return `<option value="${profile.model_profile_uuid}" ${privacyAckRequired || disabledProfile ? "disabled" : ""}>${escapeHtml(label)}</option>`;
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
      <label>Secret env var name <input name="secret_env_var_name" placeholder="MEMORIST_PROCESSING_API_KEY" value="${escapeHtml(String(form.secret_env_var_name || ""))}"></label>
      <p class="hint">Enter an environment variable name only, never a raw API key. ${this.state.editingProfileUuid && form.secret_configured ? "Leave blank to keep the existing configured secret reference." : ""}</p>
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
    this.querySelectorAll('[data-action="remove-default"]').forEach((button) => button.addEventListener("click", () => void this.removeDefault((button as HTMLElement).dataset.role as MemoristModelRole)));
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
    const payload: ModelControlProfileCreate = {
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
    if (this.state.editingProfileUuid && !payload.secret_env_var_name) {
      delete payload.secret_strategy;
      delete payload.secret_env_var_name;
    }
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
    if (profile && !isProcessingNodeProfile(profile)) {
      this.setState({ error: MAIN_CHAT_OBSERVED_NOTE });
      return;
    }
    if (profile?.is_enabled === false) {
      this.setState({ error: DISABLED_PROFILE_DEFAULT_ERROR });
      return;
    }
    if (profile && requiresPrivacyAcknowledgement(profile)) {
      this.setState({ error: PRIVACY_ACK_REQUIRED_ERROR });
      return;
    }
    if (!profile?.certification_current) {
      this.setState({
        error: `Test this exact profile successfully before setting it as default (certification: ${profile?.certification_status || "missing"}).`,
      });
      return;
    }
    try {
      await this.client.setModelControlDefault({ role, model_profile_uuid: modelProfileUuid } satisfies ModelControlRoleDefaultSet);
      await this.refresh();
      const active = this.state.effective.find((item) => item.role === role);
      if (active?.model_profile_uuid !== modelProfileUuid) {
        this.setState({ error: "The server did not confirm the intended profile as effective." });
      }
    } catch (error) {
      this.setState({ error: errorMessage(error) });
    }
  }

  private async removeDefault(role: MemoristModelRole): Promise<void> {
    try {
      await this.client.removeModelControlDefault(role);
      await this.refresh();
    } catch (error) {
      this.setState({ error: errorMessage(error) });
    }
  }

  private async testProfile(profileUuid: string): Promise<void> {
    if (this.state.testState[profileUuid] === "testing") return;
    this.state.testState[profileUuid] = "testing";
    this.render();
    try {
      const result = await this.client.testModelControlProfile(profileUuid, {
        idempotency_key: `processing-nodes-test:${profileUuid}:${Date.now()}`,
      });
      this.state.testResults[profileUuid] = result;
      this.state.testState[profileUuid] = result.health.overall_status;
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

function effectiveState(item: ModelControlEffectiveRole): string {
  if (!item.capability_compatible) return "configured but incompatible";
  if (item.inheritance_source) return `inherited from ${item.inheritance_source}`;
  if (item.scope_source === "built_in_fallback") return "built-in fallback";
  return `configured and active (${item.scope_source})`;
}

function secretState(profile: ModelControlProfile): string {
  if (!profile.secret_reference_configured) return "Not required";
  if (!profile.secret_available_in_core) return "Reference missing in Core";
  return `Available; auth ${profile.authentication_status || "not validated"}; certification ${profile.certification_status || "unknown"}`;
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char] || char);
}
