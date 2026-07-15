# Memorist UI Surface Contract

Phase 7 ships the minimum product UI contract for an Open WebUI fork/add-on without replacing Open WebUI branding.

Required surfaces:

- MemoristPanel
- FirstRunWizard
- MemoryOverviewTab
- ActiveBlocksTab
- ImportTab
- ExportTab
- CostTab
- PrivacyTab
- DiagnosticsTab
- SettingsTab

The TypeScript files in this folder describe the typed Core client and UI behavior invariants. They intentionally avoid external scripts and never render imported HTML directly.

## Processing Nodes settings page

The admin/settings surface for the Model Control Plane is implemented by the
`memorist-processing-nodes-settings` web component in `processingNodes.ts` and is
intended to be mounted at `/settings/memorist/processing-nodes`. It calls the
live `/memcore/model-control/profiles`, `/memcore/model-control/defaults`,
`/memcore/model-control/health`, and `/memcore/model-control/privacy/acknowledge`
endpoints so backend validation and operational errors remain visible inline.

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

