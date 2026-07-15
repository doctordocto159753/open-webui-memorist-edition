# Memory workflow toggle

The **Memory On / Memory Off** switch is mounted beside the Open WebUI chat
composer for a specific chat.

- **Memory On** keeps the existing Memorist workflow: the user turn may be
  captured and processed, and relevant existing memories may be retrieved and
  attached to the model context.
- **Memory Off** maps to Memorist's private turn policy. The user turn is not
  captured or queued for memory processing, retrieval is skipped, and no memory
  context is attached.
- **Memory Limited** is shown only when an existing custom `no_recall` policy
  is encountered. The PR5-B switch itself writes only On or Off.

The setting is persisted per authenticated actor and chat. Its storage key is
opaque and actor-scoped, so a browser cannot read or change another user's
setting by supplying a chat identifier. New chats default to Memory On unless a
user or system default says otherwise.

Each request carries readable workflow metadata, while the server-side policy
remains authoritative. A persisted Off setting is a consent ceiling: a forged
turn-level Full request cannot re-enable capture or attachment. Disabled turns
record only safe policy metadata and do not create a memory, message capture,
processing job, retrieval run, or attachment.

Regeneration uses the workflow state recorded on the original turn. In
particular, a response originally generated with Memory Off is regenerated
without capture or attachment even if the current composer switch is On.
The existing explicit “regenerate without recall” action continues to use its
immutable `no_recall` contract.

The toggle does not control Open WebUI's separate native-memory feature. Host
integrations mount `memorist-memory-workflow-toggle` with the current chat ID
and use the exported request helpers from `memoryWorkflowToggle.ts`.
