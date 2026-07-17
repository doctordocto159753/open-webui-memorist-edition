import { mountMemoryAttachmentForMessage } from "./memoryAttachment";
import { MemoristClient, type MemoristMessageAttachmentDisclosure } from "./memoristClient";

type DisclosureClient = Pick<MemoristClient, "messageAttachmentDisclosure">;

/**
 * Per-assistant-turn "Memory used" disclosure driven by persisted truth.
 *
 * The host renderer only knows Open WebUI message identifiers. This element
 * asks the authenticated proxy which context attachment (if any) was actually
 * delivered for the captured user turn and mounts the existing
 * `<memorist-memory-attachment>` component only when delivery truly happened.
 * Turns without an attachment render nothing at all, and a re-render for a
 * regenerated turn re-queries instead of reusing stale state.
 */
export class MemoristMessageDisclosure extends HTMLElement {
  client: DisclosureClient = new MemoristClient();

  static get observedAttributes(): string[] {
    return ["message-id", "parent-message-id", "message-done"];
  }

  private queryToken = 0;

  connectedCallback(): void {
    void this.refresh();
  }

  attributeChangedCallback(): void {
    if (this.isConnected) void this.refresh();
  }

  async refresh(): Promise<void> {
    const token = ++this.queryToken;
    // Never show a disclosure for a turn that is still generating.
    if (this.getAttribute("message-done") === "false") {
      this.replaceChildren();
      return;
    }
    const candidates = [
      this.getAttribute("parent-message-id"),
      this.getAttribute("message-id"),
    ].filter((value): value is string => Boolean(value && value.trim()));
    let disclosure: MemoristMessageAttachmentDisclosure | null = null;
    for (const candidate of candidates) {
      try {
        const result = await this.client.messageAttachmentDisclosure(candidate);
        if (result && result.disclose && result.attachment_uuid) {
          disclosure = result;
          break;
        }
      } catch {
        // Quiet by design: a failed lookup must never disturb the chat UI.
      }
    }
    if (token !== this.queryToken) return; // superseded by a newer refresh
    this.replaceChildren();
    if (!disclosure || !disclosure.attachment_uuid) return;
    mountMemoryAttachmentForMessage(this, {
      memorist_delivered_attachment_uuid: disclosure.attachment_uuid,
    });
  }
}

if (
  typeof customElements !== "undefined"
  && !customElements.get("memorist-message-disclosure")
) {
  customElements.define("memorist-message-disclosure", MemoristMessageDisclosure);
}

export function mountMessageDisclosure(
  container: HTMLElement,
  options: { messageId?: string | null; parentMessageId?: string | null; done?: boolean },
  client?: DisclosureClient,
): MemoristMessageDisclosure {
  const element = document.createElement(
    "memorist-message-disclosure",
  ) as MemoristMessageDisclosure;
  if (client) element.client = client;
  if (options.messageId) element.setAttribute("message-id", options.messageId);
  if (options.parentMessageId) element.setAttribute("parent-message-id", options.parentMessageId);
  element.setAttribute("message-done", options.done === false ? "false" : "true");
  container.append(element);
  return element;
}
