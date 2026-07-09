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
