import {
  uploadImportFile,
  type ImportDryRunReport,
  type ImportRunSummary,
  type ProcessingMode,
} from "./memoristClient";

const TERMINAL = new Set(["fully_reconstructed", "completed_with_failures", "cancelled", "failed"]);

export const MEMORIST_IMPORT_ROUTE = "/settings/memorist/import";
export const MEMORIST_IMPORT_RUN_ROUTE = "/settings/memorist/import/:importRunUuid";

export function isSupportedImportFile(file: File): boolean {
  return /\.(zip|json|jsonl)$/i.test(file.name);
}

export function importRoute(runUuid?: string): string {
  return runUuid ? `${MEMORIST_IMPORT_ROUTE}/${encodeURIComponent(runUuid)}` : MEMORIST_IMPORT_ROUTE;
}

export class MemoristImportWorkflow {
  private file: File | null = null;
  private runUuid: string | null = null;
  private pollHandle: ReturnType<typeof setInterval> | null = null;
  private dryRunShown = false;

  constructor(private readonly root: HTMLElement, private readonly baseUrl = "/memcore") {}

  mount(initialRunUuid = routeRunUuid(globalThis.location?.pathname || "")): void {
    this.root.innerHTML = `
      <section aria-labelledby="memorist-import-title">
        <h2 id="memorist-import-title">Import ChatGPT export</h2>
        <label for="memorist-import-file">Choose export file</label>
        <input id="memorist-import-file" type="file" accept=".zip,.json,.jsonl" />
        <label for="memorist-processing-mode">Processing mode</label>
        <select id="memorist-processing-mode" data-processing-mode>
          <option value="full_memory_reconstruction" selected>Full memory reconstruction</option>
          <option value="none">Import sessions/messages only</option>
          <option value="extract_candidates">Candidate extraction</option>
        </select>
        <p data-file-summary>No file selected.</p>
        <p role="status" aria-live="polite" data-status>Ready.</p>
        <progress data-progress max="100" value="0">0%</progress>
        <button type="button" data-upload disabled>Upload, inspect, reconstruct, and dry-run</button>
        <section data-plan hidden></section>
        <label><input type="checkbox" data-confirm disabled /> I reviewed the dry-run plan and approve this import.</label>
        <button type="button" data-commit disabled>Commit import</button>
        <button type="button" data-pause disabled>Pause</button>
        <button type="button" data-resume disabled>Resume</button>
        <button type="button" data-cancel disabled>Cancel</button>
        <button type="button" data-retry disabled>Retry failed</button>
        <h3>Recent import runs</h3><ul data-recent></ul>
      </section>`;
    this.input().addEventListener("change", () => this.selectFile());
    this.button("upload").addEventListener("click", () => void this.uploadAndPlan());
    this.button("commit").addEventListener("click", () => void this.commit());
    this.button("pause").addEventListener("click", () => void this.action("pause"));
    this.button("resume").addEventListener("click", () => void this.action("resume"));
    this.button("cancel").addEventListener("click", () => void this.action("cancel"));
    this.button("retry").addEventListener("click", () => void this.action("retry-failed"));
    this.root.querySelector<HTMLInputElement>("[data-confirm]")?.addEventListener("change", () => {
      this.updateCommitEnabled();
    });
    void this.loadRecent();
    if (initialRunUuid) void this.restore(initialRunUuid);
  }

  private selectFile(): void {
    const file = this.input().files?.[0] || null;
    this.file = file;
    this.dryRunShown = false;
    this.root.querySelector<HTMLInputElement>("[data-confirm]")!.checked = false;
    this.root.querySelector<HTMLInputElement>("[data-confirm]")!.disabled = true;
    this.updateCommitEnabled();
    if (!file) return;
    this.root.querySelector("[data-file-summary]")!.textContent = `${file.name} (${formatBytes(file.size)})`;
    if (!isSupportedImportFile(file)) {
      this.status("Unsupported file type. Choose a .zip, .json, or .jsonl export.");
      this.button("upload").disabled = true;
      return;
    }
    this.status("File ready to upload.");
    this.button("upload").disabled = false;
  }

  private async uploadAndPlan(): Promise<void> {
    if (!this.file) return;
    this.status("Uploading import file…");
    try {
      const run = await uploadImportFile(
        this.baseUrl,
        this.file,
        { mode: "inspect", processing_mode: this.processingMode() },
        (done, total) => this.progress(Math.round((done / Math.max(total, 1)) * 100)),
      );
      this.runUuid = run.import_run_uuid;
      history.pushState({}, "", importRoute(this.runUuid));
      this.status("Upload complete. Inspecting…");
      await this.post(`/imports/${encodeURIComponent(this.runUuid)}/inspect`, {});
      this.status("Inspection complete. Reconstructing…");
      await this.post(`/imports/${encodeURIComponent(this.runUuid)}/reconstruct`, {});
      this.status("Reconstruction complete. Preparing dry-run plan…");
      await this.post(`/imports/${encodeURIComponent(this.runUuid)}/dry-run`, {
        processing_mode: this.processingMode(),
      });
      const report = await this.get<ImportDryRunReport>(
        `/imports/${encodeURIComponent(this.runUuid)}/dry-run-report`,
      );
      this.renderDryRunReport(report);
      await this.restore(this.runUuid, false);
      this.status("Dry-run plan is ready. Review and confirm before committing.");
    } catch (error) {
      this.status(`Import planning error: ${sanitizeUiError(error)}`);
    }
  }

  private async commit(): Promise<void> {
    if (!this.runUuid || !this.dryRunShown) return;
    try {
      const mode = this.processingMode();
      this.status(`Committing import with ${mode}…`);
      await this.post(`/imports/${encodeURIComponent(this.runUuid)}/commit`, { processing_mode: mode });
      await this.restore(this.runUuid);
    } catch (error) {
      this.status(`Commit error: ${sanitizeUiError(error)}`);
    }
  }

  private async restore(runUuid: string, poll = true): Promise<void> {
    this.runUuid = runUuid;
    const run = await this.get<ImportRunSummary>(`/imports/${encodeURIComponent(runUuid)}`);
    this.renderRun(run);
    if (poll && !TERMINAL.has(run.status)) this.startPolling();
  }

  private renderRun(run: ImportRunSummary): void {
    this.status(`Import ${run.import_run_uuid} is ${run.status}.`);
    this.button("pause").disabled = run.status !== "processing" && run.status !== "committing";
    this.button("resume").disabled = run.status !== "paused";
    this.button("cancel").disabled = TERMINAL.has(run.status);
    this.button("retry").disabled = (run.error_count || 0) < 1;
  }

  private renderDryRunReport(report: ImportDryRunReport): void {
    const plan = this.root.querySelector<HTMLElement>("[data-plan]")!;
    plan.hidden = false;
    const skipReasons = report.skip_reasons || report.skip_reason_counts || {};
    const model = report.model_target || report.processing_role || {};
    plan.innerHTML = `<h3>Dry-run reconstruction plan</h3><dl>
      ${row("Source platform", report.source_platform)}
      ${row("Detected format", report.detected_format)}
      ${row("Conversations found", report.total_conversations)}
      ${row("Normalized messages", report.total_messages)}
      ${row("Eligible processing messages", report.eligible_processing_messages)}
      ${row("Skipped messages", report.skipped_messages ?? report.skipped_records)}
      ${row("Skip reasons", JSON.stringify(skipReasons))}
      ${row("Duplicate conversations/messages", report.duplicate_conversations ?? report.duplicate_messages)}
      ${row("New sessions/messages expected", `${report.expected_new_sessions ?? 0}/${report.expected_new_messages ?? 0}`)}
      ${row("Processing jobs expected", report.expected_processing_jobs)}
      ${row("Already-processed messages", report.already_processed_messages)}
      ${row("Selected processing role", model.role ?? report.selected_processing_role)}
      ${row("Model profile UUID", model.model_profile_uuid)}
      ${row("Provider type", model.provider_type)}
      ${row("Model name", model.model_name)}
      ${row("Deterministic fallback", report.deterministic_fallback)}
      ${row("Profile enabled", report.profile_enabled)}
      ${row("Privacy acknowledgement", report.privacy_acknowledged)}
      ${row("Secret availability", report.secret_available)}
      ${row("Latest health", report.latest_health)}
      ${row("Graph projection state", report.graph_projection_state)}
      ${row("Token/time warning", report.token_time_warning ?? "Full reconstruction may consume substantial time and tokens.")}
    </dl>`;
    this.dryRunShown = true;
    this.root.querySelector<HTMLInputElement>("[data-confirm]")!.disabled = false;
    this.updateCommitEnabled();
  }

  private startPolling(): void {
    if (this.pollHandle) clearInterval(this.pollHandle);
    this.pollHandle = setInterval(async () => {
      if (!this.runUuid) return;
      await this.post(`/imports/${encodeURIComponent(this.runUuid)}/process`, { batch_size: 1 });
      const progress = await this.get<ImportRunSummary & { status: string }>(
        `/imports/${encodeURIComponent(this.runUuid)}/progress`,
      );
      this.renderRun({ ...progress, import_run_uuid: this.runUuid });
      if (TERMINAL.has(progress.status) && this.pollHandle) {
        clearInterval(this.pollHandle);
        this.pollHandle = null;
        const report = await this.get<unknown>(`/imports/${encodeURIComponent(this.runUuid)}/processing-report`);
        this.status(`Import ${progress.status}. Processing report loaded.`);
        this.root.querySelector<HTMLElement>("[data-plan]")!.dataset.processingReport = JSON.stringify(report);
      }
    }, 2000);
  }

  private async action(action: string, body: unknown = {}): Promise<void> {
    if (!this.runUuid) return;
    try {
      await this.post(`/imports/${encodeURIComponent(this.runUuid)}/${action}`, body);
      await this.restore(this.runUuid);
    } catch (error) {
      this.status(`Import action error: ${sanitizeUiError(error)}`);
    }
  }

  private async loadRecent(): Promise<void> {
    const response = await this.get<{ items: ImportRunSummary[] }>("/imports?limit=10");
    const list = this.root.querySelector("[data-recent]")!;
    list.innerHTML = response.items
      .map((run) => `<li><a href="${importRoute(run.import_run_uuid)}">${escapeText(run.import_run_uuid)}</a> — ${escapeText(run.status)}</li>`)
      .join("");
  }

  private processingMode(): ProcessingMode {
    return this.root.querySelector<HTMLSelectElement>("[data-processing-mode]")!.value as ProcessingMode;
  }

  private updateCommitEnabled(): void {
    const confirmed = this.root.querySelector<HTMLInputElement>("[data-confirm]")?.checked || false;
    this.button("commit").disabled = !confirmed || !this.runUuid || !this.dryRunShown;
  }

  private input(): HTMLInputElement { return this.root.querySelector<HTMLInputElement>("#memorist-import-file")!; }
  private button(name: string): HTMLButtonElement { return this.root.querySelector<HTMLButtonElement>(`[data-${name}]`)!; }
  private status(message: string): void { this.root.querySelector("[data-status]")!.textContent = message; }
  private progress(value: number): void { const p = this.root.querySelector<HTMLProgressElement>("[data-progress]")!; p.value = value; p.textContent = `${value}%`; }
  private async get<T>(path: string): Promise<T> { const r = await fetch(`${this.baseUrl}${path}`, { credentials: "same-origin" }); if (!r.ok) throw new Error(await r.text()); return r.json() as Promise<T>; }
  private async post<T>(path: string, body: unknown): Promise<T> { const r = await fetch(`${this.baseUrl}${path}`, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); if (!r.ok) throw new Error(await r.text()); return r.json() as Promise<T>; }
}

export class MemoristImportWorkflowElement extends HTMLElement {
  connectedCallback(): void {
    new MemoristImportWorkflow(this).mount(routeRunUuid(globalThis.location?.pathname || ""));
  }
}

if (typeof customElements !== "undefined" && !customElements.get("memorist-import-workflow")) {
  customElements.define("memorist-import-workflow", MemoristImportWorkflowElement);
}

function routeRunUuid(pathname: string): string | undefined {
  const match = pathname.match(/\/settings\/memorist\/import\/([^/]+)/);
  return match ? decodeURIComponent(match[1]) : undefined;
}
function row(label: string, value: unknown): string { return `<dt>${escapeText(label)}</dt><dd>${escapeText(value === undefined || value === null || value === "" ? "Pending" : String(value))}</dd>`; }
function formatBytes(bytes: number): string { return `${bytes.toLocaleString()} bytes`; }
function sanitizeUiError(error: unknown): string { return error instanceof Error ? error.message.replace(/\b(sk-[A-Za-z0-9_-]+|api_key=[^\s&]+|token=[^\s&]+|password=[^\s&]+)\b/g, "[redacted]").replace(/\/[^\s]+/g, "[path]") : "Import failed."; }
function escapeText(value: string): string { return value.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c] || c); }
