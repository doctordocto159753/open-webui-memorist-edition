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
