import {
  MemoristClient,
  type MemoryWorkflowState,
  type MemoristTurnPolicy,
} from "./memoristClient";

type MemoryWorkflowClient = Pick<
  MemoristClient,
  "getMemoryWorkflowState" | "setMemoryWorkflowState"
>;

export type MemoryWorkflowMetadata = {
  memorist_memory_workflow?: "on" | "off" | "limited";
  memorist_capture_enabled?: boolean;
  memorist_recall_enabled?: boolean;
  memorist_attachment_enabled?: boolean;
};

export type MemoryWorkflowRequest = {
  memorist?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  [key: string]: unknown;
};

const STYLES = `
  :host {
    display: inline-flex;
    max-width: 100%;
    font: 500 .78rem/1.25 system-ui, sans-serif;
    color: var(--memorist-text, inherit);
  }
  * { box-sizing: border-box; }
  .control { display: grid; gap: .18rem; min-width: 0; }
  button {
    display: inline-flex;
    align-items: center;
    gap: .45rem;
    min-height: 2.25rem;
    max-width: 100%;
    padding: .35rem .58rem;
    border: 1px solid color-mix(in srgb, currentColor 18%, transparent);
    border-radius: 999px;
    background: color-mix(in srgb, currentColor 4%, transparent);
    color: inherit;
    cursor: pointer;
    font: inherit;
    font-weight: 650;
  }
  button:focus-visible { outline: 2px solid var(--memorist-accent, #6d5bd0); outline-offset: 2px; }
  button:disabled { cursor: not-allowed; opacity: .58; }
  .track {
    width: 1.9rem;
    height: 1.08rem;
    padding: .12rem;
    border-radius: 999px;
    background: color-mix(in srgb, currentColor 24%, transparent);
    transition: background .15s ease;
  }
  .thumb {
    display: block;
    width: .84rem;
    height: .84rem;
    border-radius: 50%;
    background: Canvas;
    box-shadow: 0 1px 2px color-mix(in srgb, currentColor 28%, transparent);
    transition: transform .15s ease;
  }
  button[aria-checked="true"] .track { background: var(--memorist-accent, #6d5bd0); }
  button[aria-checked="true"] .thumb { transform: translateX(.82rem); }
  .hint, .error { margin: 0 .2rem; max-width: 23rem; font-size: .7rem; font-weight: 400; }
  .hint { opacity: .72; }
  .error { color: #a33; }
  @media (max-width: 34rem) {
    .hint { max-width: 16rem; }
    button { min-height: 2.1rem; padding-inline: .5rem; }
  }
`;

function policyForState(state: MemoryWorkflowState): MemoristTurnPolicy {
  if (state.state === "off") return "private";
  if (state.state === "limited") return "no_recall";
  return "full";
}

function stateLabel(state: MemoryWorkflowState): string {
  if (state.state === "limited") return "Memory Limited";
  return state.memory_enabled ? "Memory On" : "Memory Off";
}

function stateHint(state: MemoryWorkflowState): string {
  if (state.state === "off") {
    return "New messages won’t be saved and existing memories won’t be attached.";
  }
  if (state.state === "limited") {
    return "This chat uses a limited memory policy. Turn on to restore capture and attachment.";
  }
  return "New messages may be saved and relevant memories may be attached for this chat.";
}

export function applyMemoryWorkflowToRequest(
  body: MemoryWorkflowRequest,
  state: MemoryWorkflowState,
): MemoryWorkflowRequest {
  const memorist = { ...(body.memorist ?? {}) };
  const metadata = { ...(body.metadata ?? {}) };
  memorist.turn_policy = policyForState(state);
  metadata.memorist_memory_workflow = state.state;
  metadata.memorist_capture_enabled = state.capture_enabled;
  metadata.memorist_recall_enabled = state.recall_enabled;
  metadata.memorist_attachment_enabled = state.attachment_enabled;
  return { ...body, memorist, metadata };
}

export function applyOriginalMemoryWorkflowToRegeneration(
  body: MemoryWorkflowRequest,
  originalMetadata: MemoryWorkflowMetadata | null | undefined,
  currentState: MemoryWorkflowState,
): MemoryWorkflowRequest {
  const original = originalMetadata?.memorist_memory_workflow;
  if (!original) return applyMemoryWorkflowToRequest(body, currentState);
  const state: MemoryWorkflowState = {
    ...currentState,
    state: original,
    memory_enabled: original === "on",
    capture_enabled:
      originalMetadata?.memorist_capture_enabled ?? original === "on",
    recall_enabled:
      originalMetadata?.memorist_recall_enabled ?? original === "on",
    attachment_enabled:
      originalMetadata?.memorist_attachment_enabled ?? original === "on",
  };
  return applyMemoryWorkflowToRequest(body, state);
}

export class MemoristMemoryWorkflowToggle extends HTMLElement {
  static observedAttributes = ["chat-uuid", "disabled"];

  client: MemoryWorkflowClient = new MemoristClient();
  private workflowState: MemoryWorkflowState | null = null;
  private loading = false;
  private saving = false;
  private error = "";
  private requestSequence = 0;

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  connectedCallback(): void {
    this.render();
    void this.load();
  }

  attributeChangedCallback(name: string): void {
    if (!this.isConnected) return;
    if (name === "chat-uuid") void this.load();
    else this.render();
  }

  set chatUuid(value: string) {
    if (value) this.setAttribute("chat-uuid", value);
    else this.removeAttribute("chat-uuid");
  }

  get chatUuid(): string {
    return this.getAttribute("chat-uuid")?.trim() ?? "";
  }

  get state(): MemoryWorkflowState | null {
    return this.workflowState ? { ...this.workflowState } : null;
  }

  async load(): Promise<void> {
    const chatUuid = this.chatUuid;
    const sequence = ++this.requestSequence;
    this.error = "";
    if (!chatUuid) {
      this.workflowState = null;
      this.loading = false;
      this.render();
      return;
    }
    this.loading = true;
    this.render();
    try {
      const state = await this.client.getMemoryWorkflowState(chatUuid);
      if (sequence !== this.requestSequence) return;
      this.workflowState = state;
    } catch {
      if (sequence !== this.requestSequence) return;
      this.error = "Memory setting could not be loaded.";
    } finally {
      if (sequence === this.requestSequence) {
        this.loading = false;
        this.render();
      }
    }
  }

  async toggle(): Promise<void> {
    if (
      !this.workflowState
      || this.loading
      || this.saving
      || this.hasAttribute("disabled")
    ) return;
    const previous = this.workflowState;
    const desired = !previous.memory_enabled;
    this.workflowState = {
      ...previous,
      memory_enabled: desired,
      capture_enabled: desired,
      recall_enabled: desired,
      attachment_enabled: desired,
      state: desired ? "on" : "off",
    };
    this.saving = true;
    this.error = "";
    this.render();
    try {
      this.workflowState = await this.client.setMemoryWorkflowState(
        this.chatUuid,
        desired,
      );
      this.dispatchEvent(new CustomEvent("memorist-memory-workflow-change", {
        bubbles: true,
        composed: true,
        detail: { state: { ...this.workflowState } },
      }));
    } catch {
      this.workflowState = previous;
      this.error = "Memory setting was not saved. Your previous setting is still active.";
      this.dispatchEvent(new CustomEvent("memorist-memory-workflow-error", {
        bubbles: true,
        composed: true,
        detail: { chatUuid: this.chatUuid },
      }));
    } finally {
      this.saving = false;
      this.render();
    }
  }

  private render(): void {
    if (!this.shadowRoot) return;
    const state = this.workflowState;
    const disabled = !state || this.loading || this.saving || this.hasAttribute("disabled");
    const label = this.loading ? "Loading Memory…" : state ? stateLabel(state) : "Memory unavailable";
    const hint = state ? stateHint(state) : "";
    this.shadowRoot.innerHTML = `
      <style>${STYLES}</style>
      <div class="control">
        <button
          type="button"
          role="switch"
          aria-checked="${state?.memory_enabled ? "true" : "false"}"
          aria-label="${label} for this chat"
          ${disabled ? "disabled" : ""}
        >
          <span class="track" aria-hidden="true"><span class="thumb"></span></span>
          <span>${label}</span>
        </button>
        ${hint ? `<p class="hint">${hint}</p>` : ""}
        ${this.error ? `<p class="error" role="status">${this.error}</p>` : ""}
      </div>
    `;
    this.shadowRoot.querySelector("button")?.addEventListener("click", () => {
      void this.toggle();
    });
  }
}

if (
  typeof customElements !== "undefined"
  && !customElements.get("memorist-memory-workflow-toggle")
) {
  customElements.define(
    "memorist-memory-workflow-toggle",
    MemoristMemoryWorkflowToggle,
  );
}

export function mountMemoryWorkflowToggleNearComposer(
  container: HTMLElement,
  chatUuid: string,
  client?: MemoryWorkflowClient,
): MemoristMemoryWorkflowToggle | undefined {
  if (!chatUuid || typeof document === "undefined") return undefined;
  const element = document.createElement(
    "memorist-memory-workflow-toggle",
  ) as MemoristMemoryWorkflowToggle;
  if (client) element.client = client;
  element.chatUuid = chatUuid;
  container.append(element);
  return element;
}
