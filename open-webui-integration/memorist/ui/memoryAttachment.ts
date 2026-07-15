import {
  MemoristClient,
  type MemoristAttachmentDisplayItem,
  type MemoristAttachmentPreview,
} from "./memoristClient";

export type MemoristMessageMetadata = {
  memorist_delivered_attachment_uuid?: string | null;
  memorist_approved_attachment_uuid?: string | null;
};

type AttachmentPreviewClient = Pick<MemoristClient, "previewAttachment">;

const MEMORY_TYPE_LABELS: Record<string, string> = {
  preference: "User preference",
  constraint: "Task constraint",
  decision: "Project decision",
  style: "Style policy",
  policy: "Policy",
  definition: "Definition",
  session_state: "Session state",
  imported_record: "Imported memory",
  tool_observation: "Tool observation",
  system_instruction: "System instruction",
  fact: "Remembered fact",
};

const SCOPE_LABELS: Record<string, string> = {
  session: "Session",
  chat: "Session",
  project: "Project",
  workspace: "Workspace",
  user: "User profile",
  user_profile: "User profile",
  style: "Style",
  policy: "Policy",
  global: "Global",
};

const SOURCE_LABELS: Record<string, string> = {
  user_explicit: "Explicitly provided by you",
  user_implied: "Inferred from your message",
  assistant_claim: "Assistant claim",
  tool_observation: "Tool observation",
  system_instruction: "System instruction",
  imported_record: "Imported record",
  automatic_capture: "Automatic capture",
  reviewed: "Reviewed memory",
};

const STATUS_LABELS: Record<string, string> = {
  accepted: "Active",
  active: "Active",
  current: "Current",
  historical: "Historical",
  manual_review: "Needs review",
  needs_review: "Needs review",
  pending_review: "Needs review",
  review: "Needs review",
  rejected: "Not used",
};

const EXPLICITNESS_LABELS: Record<string, string> = {
  explicit: "Explicit",
  implied: "Implied",
  observed: "Observed",
  imported: "Imported",
  instructed: "Instruction",
};

const SECRET_PATTERNS = [
  /\bsk-[a-zA-Z0-9_-]{8,}\b/g,
  /\beyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b/g,
  /\b(bearer\s+)[a-z0-9._~+/=-]{8,}/gi,
  /\b(api[_-]?key|access[_-]?token|token|password|secret)\s*[:=]\s*[^\s,;]{4,}/gi,
];

function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export function redactAttachmentText(value: unknown, sensitive = false): string {
  if (sensitive) return "Sensitive memory — content hidden";
  let text = String(value ?? "").replace(/\s+/g, " ").trim();
  for (const pattern of SECRET_PATTERNS) text = text.replace(pattern, "[redacted]");
  if (text.length > 280) text = `${text.slice(0, 279).trimEnd()}…`;
  return text || "Memory details available";
}

function readable(value: unknown, labels: Record<string, string>): string {
  const key = String(value ?? "").toLowerCase();
  if (!key) return "";
  return labels[key] ?? key.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatMetric(value: unknown): string {
  if (typeof value === "number") {
    if (value >= 0 && value <= 1) return `${Math.round(value * 100)}%`;
    return String(Math.round(value * 100) / 100);
  }
  return readable(value, {});
}

function formatDate(value: unknown): string {
  if (!value) return "";
  const date = new Date(String(value));
  if (Number.isNaN(date.valueOf())) return "";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function row(label: string, value: unknown): string {
  if (value === undefined || value === null || value === "") return "";
  return `<div class="detail-row"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`;
}

function metadataRows(item: MemoristAttachmentDisplayItem): string {
  const ids = item.technical_ids ?? {};
  const versions = item.metadata_versions ?? {};
  return [
    row("Memory ID", ids.memory_uuid),
    row("Memory version ID", ids.memory_version_uuid),
    row("Source candidate ID", ids.source_candidate_uuid),
    row("Route type", item.route_type),
    row("Route status", item.route_status),
    row("Gate decision", item.gate_decision),
    row("Semantic authority", item.semantic_authority),
    row("Route ID", ids.route_uuid),
    row("Annotation ID", ids.annotation_uuid),
    row("Analysis run ID", ids.analysis_run_uuid),
    row("Prompt execution ID", ids.prompt_execution_uuid),
    row("Route mapping version", versions.route_mapping_version),
    row("Candidate service version", versions.candidate_service_version),
    row("Provenance policy version", versions.provenance_policy_version),
    row("Retrieval score", formatMetric(item.retrieval_score)),
    row("Created", formatDate(item.created_at)),
    row("Updated", formatDate(item.updated_at)),
  ].join("");
}

function renderItem(item: MemoristAttachmentDisplayItem): string {
  const summary = redactAttachmentText(item.summary || item.title, item.sensitive);
  const badges = [
    readable(item.memory_type, MEMORY_TYPE_LABELS),
    readable(item.scope_type, SCOPE_LABELS),
    readable(item.status, STATUS_LABELS),
  ].filter(Boolean);
  const context = [
    item.reason ? `<p class="reason">${escapeHtml(item.reason)}</p>` : "",
    item.source_authority
      ? `<p class="source"><span>Source</span> ${escapeHtml(readable(item.source_authority, SOURCE_LABELS))}</p>`
      : "",
    item.explicitness
      ? `<p class="source"><span>Evidence</span> ${escapeHtml(readable(item.explicitness, EXPLICITNESS_LABELS))}</p>`
      : "",
  ].join("");
  const confidence = item.confidence !== undefined && item.confidence !== null
    ? `<span class="metric">Confidence ${escapeHtml(formatMetric(item.confidence))}</span>`
    : "";
  const updated = formatDate(item.updated_at || item.created_at);
  const details = metadataRows(item);

  return `
    <article class="memory-item${item.sensitive ? " sensitive" : ""}">
      <div class="badges">${badges.map((badge) => `<span class="badge">${escapeHtml(badge)}</span>`).join("")}</div>
      <p class="summary">${escapeHtml(summary)}</p>
      ${context}
      <div class="meta">${confidence}${updated ? `<time>${escapeHtml(updated)}</time>` : ""}</div>
      ${details ? `
        <details class="audit">
          <summary>Provenance and audit details</summary>
          <dl>${details}</dl>
        </details>
      ` : ""}
    </article>
  `;
}

const STYLES = `
  :host {
    display: block;
    margin: .45rem 0;
    color: var(--memorist-text, inherit);
    font: 400 .875rem/1.45 system-ui, sans-serif;
  }
  :host([hidden]) { display: none; }
  * { box-sizing: border-box; }
  .group {
    max-width: 46rem;
    border: 1px solid color-mix(in srgb, currentColor 16%, transparent);
    border-radius: .75rem;
    background: color-mix(in srgb, currentColor 3%, transparent);
    overflow: clip;
  }
  .group > summary {
    display: flex;
    align-items: center;
    gap: .55rem;
    min-height: 2.5rem;
    padding: .55rem .75rem;
    cursor: pointer;
    list-style: none;
    font-weight: 650;
  }
  .group > summary::-webkit-details-marker,
  .audit > summary::-webkit-details-marker { display: none; }
  .group > summary::before {
    content: "◆";
    font-size: .7rem;
    color: var(--memorist-accent, #6d5bd0);
  }
  .count { margin-left: auto; font-size: .78rem; font-weight: 500; opacity: .72; }
  .items { display: grid; gap: .55rem; padding: 0 .55rem .55rem; }
  .memory-item {
    min-width: 0;
    border: 1px solid color-mix(in srgb, currentColor 13%, transparent);
    border-radius: .65rem;
    padding: .7rem;
    background: var(--memorist-card, Canvas);
  }
  .memory-item.sensitive {
    border-color: color-mix(in srgb, #b7791f 45%, transparent);
  }
  .badges { display: flex; flex-wrap: wrap; gap: .3rem; margin-bottom: .4rem; }
  .badge {
    padding: .1rem .42rem;
    border-radius: 999px;
    background: color-mix(in srgb, currentColor 8%, transparent);
    font-size: .72rem;
    font-weight: 650;
  }
  p { margin: .2rem 0; overflow-wrap: anywhere; }
  .summary { font-weight: 600; }
  .reason, .source { font-size: .8rem; opacity: .78; }
  .source span { font-weight: 650; }
  .meta {
    display: flex;
    flex-wrap: wrap;
    gap: .3rem .75rem;
    margin-top: .45rem;
    font-size: .75rem;
    opacity: .7;
  }
  .audit { margin-top: .55rem; }
  .audit > summary {
    cursor: pointer;
    color: var(--memorist-accent, #5b4ab8);
    font-size: .78rem;
    font-weight: 650;
  }
  dl {
    display: grid;
    grid-template-columns: minmax(7.5rem, .45fr) minmax(0, 1fr);
    gap: .28rem .65rem;
    margin: .55rem 0 0;
    padding-top: .5rem;
    border-top: 1px solid color-mix(in srgb, currentColor 12%, transparent);
    font-size: .75rem;
  }
  .detail-row { display: contents; }
  dt { opacity: .68; }
  dd { min-width: 0; margin: 0; overflow-wrap: anywhere; font-family: ui-monospace, monospace; }
  .state {
    max-width: 46rem;
    padding: .5rem .7rem;
    border-radius: .6rem;
    background: color-mix(in srgb, currentColor 5%, transparent);
    font-size: .8rem;
  }
  .warning { color: #9a5b00; }
  @media (max-width: 36rem) {
    .group > summary { align-items: flex-start; flex-wrap: wrap; }
    .count { width: 100%; margin-left: 1.25rem; }
    dl { grid-template-columns: 1fr; gap: .08rem; }
    dd { margin-bottom: .35rem; }
  }
`;

export class MemoristMemoryAttachment extends HTMLElement {
  static observedAttributes = ["attachment-uuid"];

  client: AttachmentPreviewClient = new MemoristClient();
  private requestSequence = 0;

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  connectedCallback(): void {
    void this.load();
  }

  attributeChangedCallback(): void {
    if (this.isConnected) void this.load();
  }

  set attachmentUuid(value: string) {
    if (value) this.setAttribute("attachment-uuid", value);
    else this.removeAttribute("attachment-uuid");
  }

  get attachmentUuid(): string {
    return this.getAttribute("attachment-uuid")?.trim() ?? "";
  }

  async load(): Promise<void> {
    const attachmentUuid = this.attachmentUuid;
    const sequence = ++this.requestSequence;
    if (!attachmentUuid) {
      this.hidden = true;
      this.render("");
      return;
    }

    this.hidden = false;
    this.render(`<div class="state" role="status">Loading memory details…</div>`);
    try {
      const preview = await this.client.previewAttachment(attachmentUuid);
      if (sequence !== this.requestSequence) return;
      this.renderPreview(preview);
    } catch {
      if (sequence !== this.requestSequence) return;
      this.render(`<div class="state warning" role="status">Memory details are unavailable.</div>`);
      this.dispatchEvent(new CustomEvent("memorist-attachment-error", {
        bubbles: true,
        composed: true,
        detail: { attachmentUuid },
      }));
    }
  }

  private renderPreview(preview: MemoristAttachmentPreview): void {
    const items = Array.isArray(preview.items) ? preview.items : [];
    if (!items.length) {
      this.hidden = true;
      this.render("");
      return;
    }

    this.hidden = false;
    const count = items.length;
    this.render(`
      <details class="group">
        <summary>
          <span>Memory used</span>
          <span class="count">${count} ${count === 1 ? "memory" : "memories"} attached</span>
        </summary>
        <div class="items">${items.map(renderItem).join("")}</div>
      </details>
    `);
  }

  private render(content: string): void {
    if (!this.shadowRoot) return;
    this.shadowRoot.innerHTML = `<style>${STYLES}</style>${content}`;
  }
}

if (typeof customElements !== "undefined" && !customElements.get("memorist-memory-attachment")) {
  customElements.define("memorist-memory-attachment", MemoristMemoryAttachment);
}

export function mountMemoryAttachmentForMessage(
  container: HTMLElement,
  metadata: MemoristMessageMetadata | null | undefined,
  client?: AttachmentPreviewClient,
): MemoristMemoryAttachment | undefined {
  const attachmentUuid = metadata?.memorist_delivered_attachment_uuid
    || metadata?.memorist_approved_attachment_uuid;
  if (!attachmentUuid || typeof document === "undefined") return undefined;

  const element = document.createElement(
    "memorist-memory-attachment",
  ) as MemoristMemoryAttachment;
  if (client) element.client = client;
  element.attachmentUuid = attachmentUuid;
  container.append(element);
  return element;
}
