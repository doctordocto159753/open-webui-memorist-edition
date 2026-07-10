import { uploadImportFile, type ImportRunSummary } from "./memoristClient";

const TERMINAL = new Set(["completed", "failed", "cancelled"]);

export function isSupportedImportFile(file: File): boolean {
  return /\.(zip|json|jsonl)$/i.test(file.name);
}

export function importRoute(runUuid?: string): string {
  return runUuid ? `/settings/memorist/import/${encodeURIComponent(runUuid)}` : "/settings/memorist/import";
}

export class MemoristImportWorkflow {
  private file: File | null = null;
  private runUuid: string | null = null;
  private pollHandle: ReturnType<typeof setInterval> | null = null;

  constructor(private readonly root: HTMLElement, private readonly baseUrl = "/memcore") {}

  mount(initialRunUuid = routeRunUuid(globalThis.location?.pathname || "")): void {
    this.root.innerHTML = `
      <section aria-labelledby="memorist-import-title">
        <h2 id="memorist-import-title">Import ChatGPT export</h2>
        <label for="memorist-import-file">Choose export file</label>
        <input id="memorist-import-file" type="file" accept=".zip,.json,.jsonl" />
        <p data-file-summary>No file selected.</p>
        <p role="status" aria-live="polite" data-status>Ready.</p>
        <progress data-progress max="100" value="0">0%</progress>
        <button type="button" data-upload disabled>Upload and inspect</button>
        <section data-plan hidden></section>
        <label><input type="checkbox" data-confirm /> Import all sessions and messages and reconstruct memory for every eligible message.</label>
        <button type="button" data-commit disabled>Commit import</button>
        <button type="button" data-pause disabled>Pause</button>
        <button type="button" data-resume disabled>Resume</button>
        <button type="button" data-cancel disabled>Cancel</button>
        <button type="button" data-retry disabled>Retry failed</button>
        <h3>Recent import runs</h3><ul data-recent></ul>
      </section>`;
    this.input().addEventListener("change", () => this.selectFile());
    this.button("upload").addEventListener("click", () => void this.upload());
    this.button("commit").addEventListener("click", () => void this.action("commit", { processing_mode: "none" }));
    this.button("pause").addEventListener("click", () => void this.action("pause"));
    this.button("resume").addEventListener("click", () => void this.action("resume"));
    this.button("cancel").addEventListener("click", () => void this.action("cancel"));
    this.button("retry").addEventListener("click", () => void this.action("retry-failed"));
    this.root.querySelector<HTMLInputElement>("[data-confirm]")?.addEventListener("change", (event) => {
      this.button("commit").disabled = !(event.target as HTMLInputElement).checked || !this.runUuid;
    });
    void this.loadRecent();
    if (initialRunUuid) void this.restore(initialRunUuid);
  }

  private selectFile(): void {
    const file = this.input().files?.[0] || null;
    this.file = file;
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

  private async upload(): Promise<void> {
    if (!this.file) return;
    this.status("Uploading import file…");
    try {
      const run = await uploadImportFile(this.baseUrl, this.file, { mode: "inspect" }, (done, total) => {
        const pct = Math.round((done / Math.max(total, 1)) * 100);
        this.progress(pct);
      });
      this.runUuid = run.import_run_uuid;
      history.pushState({}, "", importRoute(this.runUuid));
      this.status("Upload complete. Inspecting…");
      await this.post(`/imports/${encodeURIComponent(this.runUuid)}/inspect`, {});
      await this.restore(this.runUuid);
    } catch (error) {
      this.status(`Upload error: ${sanitizeUiError(error)}`);
    }
  }

  private async restore(runUuid: string): Promise<void> {
    this.runUuid = runUuid;
    const run = await this.get<ImportRunSummary>(`/imports/${encodeURIComponent(runUuid)}`);
    this.renderRun(run);
    if (!TERMINAL.has(run.status)) this.startPolling();
  }

  private renderRun(run: ImportRunSummary): void {
    const plan = this.root.querySelector<HTMLElement>("[data-plan]")!;
    plan.hidden = false;
    plan.innerHTML = `<dl>
      <dt>Detected source platform</dt><dd>${escapeText(run.source_platform || "Pending")}</dd>
      <dt>Detected format</dt><dd>${escapeText(run.detected_format || "Pending")}</dd>
      <dt>Conversation count</dt><dd>${run.total_conversations || 0}</dd>
      <dt>Message count</dt><dd>${run.total_messages || 0}</dd>
      <dt>Warnings</dt><dd>${run.warning_count || 0}</dd>
      <dt>Skipped messages</dt><dd>${run.skipped_records || 0}</dd>
      <dt>Processing model</dt><dd>Selected by memory_extraction role default; deterministic fallback warning shown when no profile is configured.</dd>
      <dt>Privacy warning</dt><dd>Imported content is historical and is not trusted as current truth.</dd>
    </dl>`;
    this.status(`Import ${run.import_run_uuid} is ${run.status}.`);
    this.button("pause").disabled = run.status !== "processing" && run.status !== "committing";
    this.button("resume").disabled = run.status !== "paused";
    this.button("cancel").disabled = TERMINAL.has(run.status);
    this.button("retry").disabled = (run.error_count || 0) < 1;
  }

  private startPolling(): void {
    if (this.pollHandle) clearInterval(this.pollHandle);
    this.pollHandle = setInterval(async () => {
      if (!this.runUuid) return;
      const progress = await this.get<ImportRunSummary & { status: string }>(`/imports/${encodeURIComponent(this.runUuid)}/progress`);
      this.renderRun({ ...progress, import_run_uuid: this.runUuid });
      if (TERMINAL.has(progress.status) && this.pollHandle) clearInterval(this.pollHandle);
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
    list.innerHTML = response.items.map((run) => `<li><a href="${importRoute(run.import_run_uuid)}">${escapeText(run.import_run_uuid)}</a> — ${escapeText(run.status)}</li>`).join("");
  }

  private input(): HTMLInputElement { return this.root.querySelector<HTMLInputElement>("#memorist-import-file")!; }
  private button(name: string): HTMLButtonElement { return this.root.querySelector<HTMLButtonElement>(`[data-${name}]`)!; }
  private status(message: string): void { this.root.querySelector("[data-status]")!.textContent = message; }
  private progress(value: number): void { const p = this.root.querySelector<HTMLProgressElement>("[data-progress]")!; p.value = value; p.textContent = `${value}%`; }
  private async get<T>(path: string): Promise<T> { const r = await fetch(`${this.baseUrl}${path}`, { credentials: "same-origin" }); if (!r.ok) throw new Error(await r.text()); return r.json() as Promise<T>; }
  private async post<T>(path: string, body: unknown): Promise<T> { const r = await fetch(`${this.baseUrl}${path}`, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); if (!r.ok) throw new Error(await r.text()); return r.json() as Promise<T>; }
}

function routeRunUuid(pathname: string): string | undefined {
  const match = pathname.match(/\/settings\/memorist\/import\/([^/]+)/);
  return match ? decodeURIComponent(match[1]) : undefined;
}
function formatBytes(bytes: number): string { return `${bytes.toLocaleString()} bytes`; }
function sanitizeUiError(error: unknown): string { return error instanceof Error ? error.message.replace(/\/[^\s]+/g, "[path]") : "Import failed."; }
function escapeText(value: string): string { return value.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c] || c); }
