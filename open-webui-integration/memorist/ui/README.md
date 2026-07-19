# Memorist UI Surface Contract

PR5-G ships the first fully integrated Memorist product UI: every surface
listed in `MEMORIST_UI_SURFACES` (`surfaces.ts`) is implemented as a web
component in this folder, compiled into the derivative Open WebUI frontend
(`release/openwebui-image/`), mounted in production navigation, and covered by
integration tests. The list is a release claim — a name may only appear there
once its component is actually reachable in the shipped product.

Shipped surfaces:

- `MemoryNodeSetup` — `/settings/memorist/memory-setup` (admin)
- `MemoristProcessingNodesSettings` — `/settings/memorist/processing-nodes` (admin)
- `MemoristDiagnostics` — `/settings/memorist/diagnostics` (admin)
- `ImportTab` — `/settings/memorist/import` (admin)
- `MemoryWorkflowToggle` — mounted beside the chat composer
- `MemoryAttachment` + `MemoristMessageDisclosure` — mounted in the
  assistant-message renderer; the disclosure queries persisted delivery truth
  through `/api/v1/memorist/memory-control/messages/{id}/attachment` and stays
  silent for turns without a delivered attachment

Former aspirational surface names (MemoristPanel, FirstRunWizard,
MemoryOverviewTab, ActiveBlocksTab, ExportTab, CostTab, ModelControlTab,
PrivacyTab, DiagnosticsTab, SettingsTab) were removed from the contract in
PR5-G. They remain future work and must not be advertised until mounted.

The TypeScript files in this folder describe the typed Core client and UI behavior invariants. They intentionally avoid external scripts and never render imported HTML directly.

## Processing Nodes settings page

The admin/settings surface for the Model Control Plane is implemented by the
`memorist-processing-nodes-settings` web component in `processingNodes.ts`,
mounted at `/settings/memorist/processing-nodes`. It uses the authenticated
`/api/v1/memorist` proxy for profiles, defaults, health, role-aware provider
tests, and privacy acknowledgement, so backend validation and operational
errors remain visible inline.

## Memory used in chat

When Memorist supplies context for an assistant turn, the chat integration mounts the
`memorist-memory-attachment` component from the delivered attachment UUID in message
metadata. Its compact **Memory used** indicator shows how many memories were attached.
Expanding it shows readable memory type, scope, relevance, source, confidence, and
freshness labels. A second disclosure contains allowlisted route, gate, version, and
technical IDs for provenance and audit work.

No indicator is shown when a turn has no attached memory. Sensitive or review-bound
items use a safe summary, common credential formats are redacted at both API and UI
boundaries, and raw memory JSON/evidence is never rendered. Attachment previews remain
actor-scoped through Open WebUI's authenticated Memorist proxy; the component does not
accept browser-supplied identity headers.


## Memory control beside the composer

The host chat integration mounts `memorist-memory-workflow-toggle` with the
authenticated chat ID. **Memory On** permits both capture/processing and
retrieval/attachment. **Memory Off** disables both for that chat and explains
the privacy effect before send. The state is persisted per actor and chat,
rolls back visually when a save fails, and regeneration helpers preserve the
original turn's setting. See [The Memory Machine — consent boundary](../../../docs/MEMORY_MACHINE.md#the-consent-boundary-memory-on--memory-off).


## First-run memory node setup

Mount `memorist-memory-node-setup` at
`/settings/memorist/memory-setup` for verified Open WebUI administrators. It
reports the real local fallback state and configures the existing role-based
Model Control profiles through the authenticated proxy. Remote providers use an
environment-variable secret reference; raw API keys are never accepted or
returned. See [Installation — memory processing and API keys](../../../docs/INSTALLATION.md#memory-processing-and-api-keys) and [Model Control Plane reference](../../../docs/reference/model-control-plane.md).
