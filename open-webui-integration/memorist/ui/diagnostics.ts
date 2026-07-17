import { MemoristClient, type MemoristOpenWebUIDiagnostics } from "./memoristClient";

export const MEMORIST_DIAGNOSTICS_ROUTE = "/settings/memorist/diagnostics";

type DiagnosticsClient = Pick<MemoristClient, "openwebuiDiagnostics" | "memoryNodeSetupStatus">;

type DiagnosticsState = {
  loading: boolean;
  error: string | null;
  unauthorized: boolean;
  diagnostics: MemoristOpenWebUIDiagnostics | null;
  setupComplete: boolean | null;
  configuredRoles: number | null;
};

export class MemoristDiagnostics extends HTMLElement {
  client: DiagnosticsClient = new MemoristClient();

  private state: DiagnosticsState = {
    loading: true,
    error: null,
    unauthorized: false,
    diagnostics: null,
    setupComplete: null,
    configuredRoles: null,
  };

  connectedCallback(): void {
    void this.refresh();
  }

  async refresh(): Promise<void> {
    this.setState({ loading: true, error: null, unauthorized: false });
    try {
      const diagnostics = await this.client.openwebuiDiagnostics();
      let setupComplete: boolean | null = null;
      let configuredRoles: number | null = null;
      try {
        const setup = await this.client.memoryNodeSetupStatus();
        setupComplete = Boolean(setup.ready_for_memory_processing);
        configuredRoles = Array.isArray(setup.configured_roles)
          ? setup.configured_roles.length
          : null;
      } catch {
        // Setup summary is optional; diagnostics stays useful without it.
      }
      this.setState({ loading: false, diagnostics, setupComplete, configuredRoles });
    } catch (error) {
      const message = safeErrorText(error);
      this.setState({
        loading: false,
        unauthorized: /401|403|admin|unauthorized|forbidden/i.test(message),
        error: message,
      });
    }
  }

  private setState(patch: Partial<DiagnosticsState>): void {
    this.state = { ...this.state, ...patch };
    this.render();
  }

  render(): void {
    if (this.state.loading) {
      this.innerHTML = `<section class="memorist-diagnostics" aria-busy="true"><p>Loading Memorist diagnostics…</p></section>`;
      return;
    }
    if (this.state.unauthorized) {
      this.innerHTML = `<section class="memorist-diagnostics"><div role="alert" class="error">Administrator access is required to view Memorist diagnostics.</div></section>`;
      return;
    }
    if (this.state.error || !this.state.diagnostics) {
      this.innerHTML = `<section class="memorist-diagnostics">
        <div role="alert" class="error">${escapeHtml(this.state.error || "Diagnostics are unavailable.")}</div>
        <button type="button" data-action="retry">Retry</button>
      </section>`;
      this.bindEvents();
      return;
    }
    const diagnostics = this.state.diagnostics;
    const health = diagnostics.core_health || {};
    const integration = diagnostics.integration || {};
    const warnings = Array.isArray(health.profile_warnings) ? health.profile_warnings : [];
    const certification = health.full_mode_certification || "unknown";
    const rows: Array<[string, string]> = [
      ["Open WebUI integration", integration.route_order?.ordered_before_spa ? "connected (authenticated proxy reachable)" : "route order not verified"],
      ["Managed chat filter", integration.filter?.filter_id ? `${integration.filter.filter_id} (${integration.filter.action || "verified"})` : "not reported"],
      ["Memorist Core", String(health.status || "unknown")],
      ["Runtime mode", String(health.runtime_profile || "unknown")],
      ["Canonical store", String(health.canonical_store || "unknown")],
      ["Graph backend", `${health.graph_backend || "unknown"} (${health.graph_status || "unknown"})`],
      ["Scheduler", String(health.hot_scheduler || health.scheduler || "unknown")],
      ["Profile warnings", warnings.length ? warnings.join("; ") : "none"],
      [
        "Processing setup",
        this.state.setupComplete == null
          ? "unavailable"
          : `${this.state.setupComplete ? "ready" : "not ready"}${this.state.configuredRoles == null ? "" : ` (${this.state.configuredRoles} role defaults configured)`}`,
      ],
      ["Open WebUI version", diagnostics.openwebui_version],
      ["Full-mode certification", certification],
    ];
    const certificationNote = health.full_mode_certification_note
      ? `<p class="note">${escapeHtml(health.full_mode_certification_note)}</p>`
      : "";
    this.innerHTML = `<section class="memorist-diagnostics">
      <header>
        <p class="eyebrow">Memorist admin settings</p>
        <h1>Diagnostics</h1>
        <p>Read-only runtime status of the Memorist integration. No secrets, connection strings, or tokens are shown here.</p>
      </header>
      <div class="toolbar"><button type="button" data-action="retry">Refresh</button></div>
      <dl class="diagnostics-grid">
        ${rows
          .map(
            ([label, value]) =>
              `<div class="row"><dt>${escapeHtml(label)}</dt><dd data-diagnostic="${escapeHtml(slug(label))}">${escapeHtml(value)}</dd></div>`,
          )
          .join("")}
      </dl>
      ${certificationNote}
    </section>`;
    this.bindEvents();
  }

  private bindEvents(): void {
    this.querySelector('[data-action="retry"]')?.addEventListener("click", () => {
      void this.refresh();
    });
  }
}

function slug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-");
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function safeErrorText(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error);
  // Never render tokens, keys, or DSN-looking values from transport errors.
  return raw.replace(/[A-Za-z0-9_\-]{24,}/g, "[redacted]").slice(0, 400);
}

if (typeof customElements !== "undefined" && !customElements.get("memorist-diagnostics")) {
  customElements.define("memorist-diagnostics", MemoristDiagnostics);
}

export function mountMemoristDiagnostics(
  container: HTMLElement,
  client?: DiagnosticsClient,
): MemoristDiagnostics {
  const element = document.createElement("memorist-diagnostics") as MemoristDiagnostics;
  if (client) element.client = client;
  container.append(element);
  return element;
}
