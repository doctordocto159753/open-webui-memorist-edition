import { uploadImportFile, type ImportDryRunReport, type ImportProcessingReport, type ImportProgress, type ImportRunStatus, type ImportRunSummary } from "./memoristClient";

export type ImportWorkflowPhase = "idle" | "uploading" | "inspecting" | "reconstructing" | "dry_running" | "awaiting_confirmation" | "committing" | "processing" | "paused" | "committed" | "fully_reconstructed" | "completed_with_failures" | "cancelled" | "failed";

const TERMINAL = new Set<ImportRunStatus>(["committed", "fully_reconstructed", "completed_with_failures", "completed", "failed", "cancelled"]);
const REPORT_TERMINAL = new Set<ImportRunStatus>(["fully_reconstructed", "completed_with_failures", "completed"]);
const PROCESSING_MODE_FULL = "full_memory_reconstruction";

export function isSupportedImportFile(file: File): boolean { return /\.(zip|json|jsonl)$/i.test(file.name); }
export function importRoute(runUuid?: string): string { return runUuid ? `/settings/memorist/import/${encodeURIComponent(runUuid)}` : "/settings/memorist/import"; }

export class MemoristImportWorkflow {
  private file: File | null = null;
  private runUuid: string | null = null;
  private phase: ImportWorkflowPhase = "idle";
  private pollTimer: ReturnType<typeof setTimeout> | null = null;
  private driveTimer: ReturnType<typeof setTimeout> | null = null;
  private pollInFlight = false;
  private driveInFlight = false;
  private disconnected = false;
  private abortController: AbortController | null = null;
  private approvedProcessingMode: string | null = null;
  private processingMode = PROCESSING_MODE_FULL;
  private processingFailureCount = 0;

  constructor(private readonly root: HTMLElement, private readonly baseUrl = "/memcore") {}

  mount(initialRunUuid = routeRunUuid(globalThis.location?.pathname || "")): void {
    this.disconnected = false;
    this.root.innerHTML = `
      <section aria-labelledby="memorist-import-title" data-phase="idle">
        <h2 id="memorist-import-title">Import ChatGPT export</h2>
        <label for="memorist-import-file">Choose export file</label>
        <input id="memorist-import-file" type="file" accept=".zip,.json,.jsonl" />
        <p data-file-summary>No file selected.</p>
        <label for="memorist-processing-mode">Processing mode</label>
        <select id="memorist-processing-mode" data-processing-mode>
          <option value="full_memory_reconstruction">Full memory reconstruction</option>
          <option value="extract_candidates">Extract candidates only</option>
          <option value="none">Import sessions/messages only</option>
        </select>
        <p role="status" aria-live="polite" data-status>Ready.</p>
        <progress data-progress max="100" value="0">0%</progress>
        <button type="button" data-upload disabled>Upload and inspect</button>
        <button type="button" data-inspect disabled>Inspect</button>
        <button type="button" data-reconstruct disabled>Reconstruct</button>
        <button type="button" data-dry-run disabled>Dry run</button>
        <section data-plan hidden></section>
        <label><input type="checkbox" data-confirm disabled /> I approve this dry-run plan and processing mode.</label>
        <button type="button" data-commit disabled>Commit import</button>
        <button type="button" data-pause disabled>Pause</button><button type="button" data-resume disabled>Resume</button>
        <button type="button" data-cancel disabled>Cancel</button><button type="button" data-retry disabled>Retry failed</button>
        <section data-processing-report hidden></section>
        <h3>Recent import runs</h3><ul data-recent></ul>
      </section>`;
    this.input().addEventListener("change", () => this.selectFile());
    this.button("upload").addEventListener("click", () => void this.upload());
    this.button("inspect").addEventListener("click", () => void this.inspect());
    this.button("reconstruct").addEventListener("click", () => void this.reconstruct());
    this.button("dry-run").addEventListener("click", () => void this.dryRun());
    this.button("commit").addEventListener("click", () => void this.commit());
    this.button("pause").addEventListener("click", () => void this.action("pause"));
    this.button("resume").addEventListener("click", () => void this.action("resume"));
    this.button("cancel").addEventListener("click", () => void this.action("cancel"));
    this.button("retry").addEventListener("click", () => void this.action("retry-failed"));
    this.confirm().addEventListener("change", () => this.updateCommitState());
    this.modeSelect().addEventListener("change", () => this.changeMode());
    void this.loadRecent();
    if (initialRunUuid) void this.restore(initialRunUuid);
  }

  disconnectedCallback(): void { this.disconnected = true; this.stopLoops(); }

  private setPhase(phase: ImportWorkflowPhase): void { this.phase = phase; this.root.firstElementChild?.setAttribute("data-phase", phase); }
  private selectFile(): void { const file = this.input().files?.[0] || null; this.file = file; if (!file) return; this.root.querySelector("[data-file-summary]")!.textContent = `${file.name} (${formatBytes(file.size)})`; this.button("upload").disabled = !isSupportedImportFile(file); this.status(isSupportedImportFile(file) ? "File ready to upload." : "Unsupported file type. Choose a .zip, .json, or .jsonl export."); }
  private async upload(): Promise<void> { if (!this.file) return; this.setPhase("uploading"); this.status("Uploading import file…"); try { const run = await uploadImportFile(this.baseUrl, this.file, { mode: "inspect", processing_mode: this.processingMode }, (done, total) => this.progress(Math.round((done / Math.max(total, 1)) * 100))); this.runUuid = run.import_run_uuid; history.pushState({}, "", importRoute(this.runUuid)); await this.inspect(); } catch (error) { this.fail(error, "Upload error"); } }
  private async inspect(): Promise<void> { if (!this.runUuid) return; this.setPhase("inspecting"); this.status("Inspecting…"); try { await this.post(`/imports/${encodeURIComponent(this.runUuid)}/inspect`, {}); await this.reconstruct(); } catch (error) { this.fail(error, "Inspect error"); } }
  private async reconstruct(): Promise<void> { if (!this.runUuid) return; this.setPhase("reconstructing"); this.status("Reconstructing…"); try { await this.post(`/imports/${encodeURIComponent(this.runUuid)}/reconstruct`, {}); await this.dryRun(); } catch (error) { this.fail(error, "Reconstruct error"); } }
  private async dryRun(): Promise<void> { if (!this.runUuid) return; this.setPhase("dry_running"); this.status("Building dry-run report…"); try { await this.post(`/imports/${encodeURIComponent(this.runUuid)}/dry-run`, { processing_mode: this.processingMode }); const report = await this.get<ImportDryRunReport>(`/imports/${encodeURIComponent(this.runUuid)}/dry-run-report`); this.approvedProcessingMode = report.processing_mode || this.processingMode; this.processingMode = this.approvedProcessingMode; this.modeSelect().value = this.processingMode; this.renderDryRunReport(report); this.setPhase("awaiting_confirmation"); this.confirm().disabled = false; this.button("dry-run").disabled = false; this.updateCommitState(); } catch (error) { this.fail(error, "Dry-run error"); } }
  private async commit(): Promise<void> { if (!this.runUuid || !this.approvedProcessingMode) return; this.setPhase("committing"); this.status("Committing approved import…"); try { const run = await this.post<ImportRunSummary>(`/imports/${encodeURIComponent(this.runUuid)}/commit`, { processing_mode: this.approvedProcessingMode }); this.renderRun(run); await this.restore(this.runUuid); } catch (error) { this.fail(error, "Commit error"); } }

  private async restore(runUuid: string): Promise<void> { this.runUuid = runUuid; this.stopLoops(); const run = await this.get<ImportRunSummary>(`/imports/${encodeURIComponent(runUuid)}`); this.renderRun(run); if (run.status === "dry_run") { const report = await this.get<ImportDryRunReport>(`/imports/${encodeURIComponent(runUuid)}/dry-run-report`); this.approvedProcessingMode = report.processing_mode || null; if (this.approvedProcessingMode) { this.processingMode = this.approvedProcessingMode; this.modeSelect().value = this.processingMode; } this.renderDryRunReport(report); this.setPhase("awaiting_confirmation"); this.confirm().disabled = false; this.updateCommitState(); return; } if (run.status === "processing" || run.status === "committing") { this.pollProgress(); if (run.status === "processing") this.driveBoundedProcessingTemporarily(); } if (REPORT_TERMINAL.has(run.status)) await this.loadProcessingReport(); }

  private renderRun(run: ImportRunSummary | ImportProgress): void {
    this.runUuid = run.import_run_uuid || this.runUuid; const status = run.status; this.status(`Import ${this.runUuid} is ${status}.`);
    if (status === "uploaded" || status === "staged") { this.setPhase("idle"); this.button("inspect").disabled = false; }
    if (status === "inspected") { this.setPhase("inspecting"); this.button("reconstruct").disabled = false; }
    if (status === "reconstructed") { this.setPhase("reconstructing"); this.button("dry-run").disabled = false; }
    if (status === "processing" || status === "committing") this.setPhase(status === "processing" ? "processing" : "committing");
    if (status === "paused") this.setPhase("paused"); if (status === "committed") { this.setPhase("committed"); this.status("Import committed. Sessions and messages were imported without full reconstruction."); }
    if (status === "fully_reconstructed" || status === "completed") this.setPhase("fully_reconstructed"); if (status === "completed_with_failures") this.setPhase("completed_with_failures"); if (status === "cancelled") this.setPhase("cancelled"); if (status === "failed") this.setPhase("failed");
    this.button("pause").disabled = status !== "processing" && status !== "committing"; this.button("resume").disabled = status !== "paused"; this.button("cancel").disabled = TERMINAL.has(status); this.button("retry").disabled = this.processingFailureCount < 1;
  }

  private renderDryRunReport(report: ImportDryRunReport): void { const plan = this.root.querySelector<HTMLElement>("[data-plan]")!; plan.hidden = false; plan.innerHTML = `<h3>Dry-run report</h3><dl>
    ${row("Source platform", report.source_platform_display ?? report.source_platform)}${row("Detected format", report.detected_format)}${row("Conversation count", report.conversation_count)}${row("Message count", report.message_count)}${row("Eligible processing messages", report.eligible_processing_message_count)}${row("Ineligible processing messages", report.ineligible_processing_message_count)}${row("Duplicate conversations", report.duplicate_conversation_count)}${row("Duplicate messages", report.duplicate_message_count)}${row("Expected sessions", report.expected_new_database_rows?.sessions)}${row("Expected messages", report.expected_new_database_rows?.messages)}${row("Expected import mappings", report.expected_new_database_rows?.import_mappings)}${row("Expected text unitization jobs", report.expected_text_unitization_jobs)}${row("Expected memory processing jobs", report.expected_memory_processing_jobs)}${row("Processing mode", report.processing_mode)}${row("Processing model role", report.processing_model?.role)}${row("Processing model", report.processing_model?.model_name)}${row("Deterministic fallback", report.deterministic_fallback)}${row("Graph projection enabled", report.graph_projection_enabled)}${row("Large reconstruction warning", report.large_reconstruction_warning)}${row("Skip reasons", formatReasons(report.skip_reasons))}</dl>`; }
  private async loadProcessingReport(): Promise<void> { if (!this.runUuid) return; const report = await this.get<ImportProcessingReport>(`/imports/${encodeURIComponent(this.runUuid)}/processing-report`); this.renderProcessingReport(report); }
  private renderProcessingReport(report: ImportProcessingReport): void { this.processingFailureCount = report.processing_jobs_failed ?? report.failed_messages ?? 0; const el = this.root.querySelector<HTMLElement>("[data-processing-report]")!; el.hidden = false; el.innerHTML = `<h3>Processing report</h3><dl>${row("Imported conversations", report.imported_conversations)}${row("Imported messages", report.imported_messages)}${row("Total processing jobs", report.processing_jobs_total)}${row("Queued", (report.processing_jobs_queued ?? 0) + (report.processing_jobs_pending ?? 0))}${row("Running", report.processing_jobs_running)}${row("Succeeded", report.processing_jobs_succeeded)}${row("Failed", report.processing_jobs_failed)}${row("Skipped", report.processing_jobs_skipped)}${row("Already processed", report.processing_jobs_already_processed)}${row("Memory candidates", report.memory_candidates)}${row("Memory versions", report.memory_versions)}${row("Prompt executions", report.prompt_executions)}${row("Usage events", report.usage_events)}${row("Input tokens", report.input_tokens)}${row("Output tokens", report.output_tokens)}${row("Graph projection events", report.graph_projection_events)}${row("Skip reasons", formatReasons(report.skip_reasons))}${row("Sanitized last error", sanitizeUiError(report.sanitized_last_error || "Not reported"))}${row("Final status", report.final_status)}</dl>`; this.button("retry").disabled = this.processingFailureCount < 1; }

  pollProgress(): void { if (!this.runUuid || this.pollTimer || this.disconnected) return; const tick = async () => { if (!this.runUuid || this.disconnected || this.pollInFlight) return; this.pollInFlight = true; this.abortController = new AbortController(); try { const progress = await this.get<ImportProgress>(`/imports/${encodeURIComponent(this.runUuid)}/progress`, this.abortController.signal); this.renderRun(progress); if (REPORT_TERMINAL.has(progress.status)) await this.loadProcessingReport(); if (!TERMINAL.has(progress.status) && progress.status !== "paused" && progress.status !== "cancelled" && !this.disconnected) this.pollTimer = setTimeout(tick, 2000); } catch (error) { if (!this.disconnected) { this.status(`Polling delayed: ${sanitizeUiError(error)}`); this.pollTimer = setTimeout(tick, 3000); } } finally { this.pollInFlight = false; } }; this.pollTimer = setTimeout(tick, 0); }
  driveBoundedProcessingTemporarily(): void { if (!this.runUuid || this.driveTimer || this.disconnected) return; let remaining = 3; const tick = async () => { if (!this.runUuid || this.disconnected || this.phase !== "processing" || this.driveInFlight || remaining-- <= 0) return; this.driveInFlight = true; try { await this.post(`/imports/${encodeURIComponent(this.runUuid)}/process`, { batch_size: 25 }); } catch { /* temporary driver is best effort */ } finally { this.driveInFlight = false; if (!this.disconnected && this.phase === "processing" && remaining > 0) this.driveTimer = setTimeout(tick, 2500); } }; this.driveTimer = setTimeout(tick, 0); }
  private stopLoops(): void { if (this.pollTimer) clearTimeout(this.pollTimer); if (this.driveTimer) clearTimeout(this.driveTimer); this.pollTimer = null; this.driveTimer = null; this.abortController?.abort(); this.abortController = null; }
  private changeMode(): void { this.processingMode = this.modeSelect().value; if (this.approvedProcessingMode && this.processingMode !== this.approvedProcessingMode) { this.approvedProcessingMode = null; this.confirm().checked = false; this.confirm().disabled = true; this.button("commit").disabled = true; this.status("Processing mode changed. Dry-run approval was invalidated; run dry-run again."); } }
  private updateCommitState(): void { this.button("commit").disabled = !this.runUuid || !this.confirm().checked || !this.approvedProcessingMode; }
  private async action(action: string, body: unknown = {}): Promise<void> { if (!this.runUuid) return; try { await this.post(`/imports/${encodeURIComponent(this.runUuid)}/${action}`, body); await this.restore(this.runUuid); } catch (error) { this.fail(error, "Import action error"); } }
  private fail(error: unknown, prefix: string): void { this.setPhase("failed"); this.status(`${prefix}: ${sanitizeUiError(error)}`); }
  private async loadRecent(): Promise<void> { try { const response = await this.get<{ items: ImportRunSummary[] }>("/imports?limit=10"); this.root.querySelector("[data-recent]")!.innerHTML = response.items.map((run) => `<li><a href="${importRoute(run.import_run_uuid)}">${escapeText(run.import_run_uuid)}</a> — ${escapeText(run.status)}</li>`).join(""); } catch { } }
  private input(): HTMLInputElement { return this.root.querySelector<HTMLInputElement>("#memorist-import-file") ?? this.root.querySelector<HTMLInputElement>("input[type=file]")!; } private modeSelect(): HTMLSelectElement { return this.root.querySelector<HTMLSelectElement>("[data-processing-mode]")!; } private confirm(): HTMLInputElement { return this.root.querySelector<HTMLInputElement>("[data-confirm]")!; } private button(name: string): HTMLButtonElement { return this.root.querySelector<HTMLButtonElement>(`[data-${name}]`)!; } private status(message: string): void { this.root.querySelector("[data-status]")!.textContent = message; } private progress(value: number): void { const p = this.root.querySelector<HTMLProgressElement>("[data-progress]")!; p.value = value; p.textContent = `${value}%`; }
  private async get<T>(path: string, signal?: AbortSignal): Promise<T> { const r = await fetch(`${this.baseUrl}${path}`, { credentials: "same-origin", signal }); if (!r.ok) throw new Error(await r.text()); return r.json() as Promise<T>; }
  private async post<T>(path: string, body: unknown): Promise<T> { const r = await fetch(`${this.baseUrl}${path}`, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); if (!r.ok) throw new Error(await r.text()); return r.json() as Promise<T>; }
}

export class MemoristImportWorkflowElement extends HTMLElement { private workflow?: MemoristImportWorkflow; connectedCallback(): void { this.workflow = new MemoristImportWorkflow(this); this.workflow.mount(); } disconnectedCallback(): void { this.workflow?.disconnectedCallback(); } }
if (typeof customElements !== "undefined" && !customElements.get("memorist-import-workflow")) customElements.define("memorist-import-workflow", MemoristImportWorkflowElement);
function routeRunUuid(pathname: string): string | undefined { const match = pathname.match(/\/settings\/memorist\/import\/([^/]+)/); return match ? decodeURIComponent(match[1]) : undefined; }
function formatBytes(bytes: number): string { return `${bytes.toLocaleString()} bytes`; }
function sanitizeUiError(error: unknown): string { const text = error instanceof Error ? error.message : String(error || "Import failed."); return text.replace(/Bearer\s+[A-Za-z0-9._-]+/gi, "Bearer [redacted]").replace(/(api[_-]?key=)[^\s&]+/gi, "$1[redacted]").replace(/sk-[A-Za-z0-9_-]+/g, "[redacted]").replace(/\/[^\s]+/g, "[path]"); }
function escapeText(value: string): string { return value.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c] || c); }
function display(value: unknown): string { if (value === undefined || value === null || value === "") return "Not reported"; if (typeof value === "boolean") return value ? "Yes" : "No"; return String(value); }
function row(label: string, value: unknown): string { return `<dt>${escapeText(label)}</dt><dd>${escapeText(display(value))}</dd>`; }
function formatReasons(value?: Record<string, number>): string { return value && Object.keys(value).length ? Object.entries(value).map(([k, v]) => `${k}: ${v}`).join(", ") : "Not reported"; }
