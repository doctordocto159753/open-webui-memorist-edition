import { MemoristClient, type ImportProcessingMode } from "./memoristClient";

export const MEMORIST_IMPORT_WORKFLOW_ROUTE = "/settings/memorist/import";
const CONFIRM_FULL_RECONSTRUCTION =
  "Import all sessions and messages and reconstruct memory for every eligible message.";

type ImportWorkflowState = {
  importRunUuid?: string;
  archivePath: string;
  processingMode: ImportProcessingMode;
  busy: boolean;
  error?: string;
  report?: Record<string, unknown>;
  progress?: Record<string, unknown>;
};

export class MemoristImportWorkflow extends HTMLElement {
  private readonly client = new MemoristClient();
  private refreshTimer?: number;
  private state: ImportWorkflowState = {
    archivePath: "",
    processingMode: "full_memory_reconstruction",
    busy: false,
  };

  connectedCallback() {
    this.render();
  }

  disconnectedCallback() {
    if (this.refreshTimer) window.clearInterval(this.refreshTimer);
  }

  private setState(next: Partial<ImportWorkflowState>) {
    this.state = { ...this.state, ...next };
    this.render();
  }

  private async run(action: () => Promise<Record<string, unknown>>) {
    this.setState({ busy: true, error: undefined });
    try {
      const result = await action();
      this.setState({ busy: false, report: result });
      return result;
    } catch (error) {
      this.setState({ busy: false, error: String(error) });
      return undefined;
    }
  }

  private async stageInspectAndPlan() {
    const uploaded = await this.run(() =>
      this.client.uploadImport({
        archive_path: this.state.archivePath,
        mode: "inspect",
        options: { processing_mode: this.state.processingMode },
      }),
    );
    const importRunUuid = String(uploaded?.import_run_uuid || "");
    if (!importRunUuid) return;
    this.setState({ importRunUuid });
    await this.run(() => this.client.inspectImport(importRunUuid));
    await this.run(() => this.client.reconstructImport(importRunUuid));
    await this.run(() =>
      this.client.dryRunImport(importRunUuid, { processing_mode: this.state.processingMode }),
    );
  }

  private async confirmCommit() {
    if (!this.state.importRunUuid) return;
    const confirmed = window.confirm(CONFIRM_FULL_RECONSTRUCTION);
    if (!confirmed) return;
    await this.run(() =>
      this.client.commitImport(this.state.importRunUuid!, {
        processing_mode: this.state.processingMode,
      }),
    );
    await this.refreshProgress();
    this.startProgressRefresh();
  }

  private startProgressRefresh() {
    if (this.refreshTimer) window.clearInterval(this.refreshTimer);
    this.refreshTimer = window.setInterval(() => void this.refreshProgress(), 2500);
  }

  private async refreshProgress() {
    if (!this.state.importRunUuid) return;
    const progress = await this.client.importProgress(this.state.importRunUuid);
    this.setState({ progress });
  }

  private async control(action: "pause" | "resume" | "cancel" | "retry") {
    if (!this.state.importRunUuid) return;
    const runId = this.state.importRunUuid;
    const request =
      action === "pause"
        ? this.client.pauseImport(runId)
        : action === "resume"
          ? this.client.resumeImport(runId)
          : action === "cancel"
            ? this.client.cancelImport(runId)
            : this.client.retryFailedImport(runId);
    await this.run(() => request);
    await this.refreshProgress();
  }

  private render() {
    const disabled = this.state.busy ? "disabled" : "";
    const progress = this.state.progress || {};
    this.innerHTML = `
      <section data-testid="memorist-import-workflow">
        <label>
          ChatGPT/OpenAI export ZIP or conversations.json
          <input id="memorist-import-source" value="${escapeHtml(this.state.archivePath)}" placeholder="C:\\path\\to\\conversations.json" />
        </label>
        <label>
          Processing mode
          <select id="memorist-import-mode" ${disabled}>
            <option value="full_memory_reconstruction" ${selected(this.state.processingMode, "full_memory_reconstruction")}>Full memory reconstruction</option>
            <option value="extract_candidates" ${selected(this.state.processingMode, "extract_candidates")}>Extract candidates only</option>
            <option value="none" ${selected(this.state.processingMode, "none")}>Import sessions/messages only</option>
          </select>
        </label>
        <p data-testid="memorist-import-token-warning">Full memory reconstruction can take a long time and use substantial tokens.</p>
        <button id="memorist-import-plan" ${disabled}>Upload, inspect, reconstruct, dry-run</button>
        <button id="memorist-import-confirm" ${disabled}>Confirm import</button>
        <button id="memorist-import-pause" ${disabled}>Pause</button>
        <button id="memorist-import-resume" ${disabled}>Resume</button>
        <button id="memorist-import-cancel" ${disabled}>Cancel</button>
        <button id="memorist-import-retry" ${disabled}>Retry failed</button>
        <dl data-testid="memorist-import-progress">
          <dt>Status</dt><dd>${escapeHtml(String(progress.status || ""))}</dd>
          <dt>Committed messages</dt><dd>${escapeHtml(String(progress.messages_committed || 0))}</dd>
          <dt>Queued</dt><dd>${escapeHtml(String(progress.processing_jobs_queued || 0))}</dd>
          <dt>Running</dt><dd>${escapeHtml(String(progress.processing_jobs_running || 0))}</dd>
          <dt>Succeeded</dt><dd>${escapeHtml(String(progress.processing_jobs_succeeded || 0))}</dd>
          <dt>Failed</dt><dd>${escapeHtml(String(progress.processing_jobs_failed || 0))}</dd>
          <dt>Skipped</dt><dd>${escapeHtml(String(progress.processing_jobs_skipped || 0))}</dd>
          <dt>Input tokens</dt><dd>${escapeHtml(String(progress.input_tokens || 0))}</dd>
          <dt>Output tokens</dt><dd>${escapeHtml(String(progress.output_tokens || 0))}</dd>
        </dl>
        <pre data-testid="memorist-import-report">${escapeHtml(JSON.stringify(this.state.report || {}, null, 2))}</pre>
        <pre data-testid="memorist-import-error">${escapeHtml(this.state.error || "")}</pre>
      </section>
    `;
    this.bind();
  }

  private bind() {
    this.querySelector<HTMLInputElement>("#memorist-import-source")?.addEventListener(
      "input",
      (event) =>
        this.setState({ archivePath: (event.target as HTMLInputElement).value }),
    );
    this.querySelector<HTMLSelectElement>("#memorist-import-mode")?.addEventListener(
      "change",
      (event) =>
        this.setState({
          processingMode: (event.target as HTMLSelectElement).value as ImportProcessingMode,
        }),
    );
    this.querySelector("#memorist-import-plan")?.addEventListener(
      "click",
      () => void this.stageInspectAndPlan(),
    );
    this.querySelector("#memorist-import-confirm")?.addEventListener(
      "click",
      () => void this.confirmCommit(),
    );
    this.querySelector("#memorist-import-pause")?.addEventListener(
      "click",
      () => void this.control("pause"),
    );
    this.querySelector("#memorist-import-resume")?.addEventListener(
      "click",
      () => void this.control("resume"),
    );
    this.querySelector("#memorist-import-cancel")?.addEventListener(
      "click",
      () => void this.control("cancel"),
    );
    this.querySelector("#memorist-import-retry")?.addEventListener(
      "click",
      () => void this.control("retry"),
    );
  }
}

function selected(current: ImportProcessingMode, candidate: ImportProcessingMode): string {
  return current === candidate ? "selected" : "";
}

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

if (typeof customElements !== "undefined" && !customElements.get("memorist-import-workflow")) {
  customElements.define("memorist-import-workflow", MemoristImportWorkflow);
}
