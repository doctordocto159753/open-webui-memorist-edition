export const MEMORIST_UI_SURFACES = [
  "MemoristPanel",
  "FirstRunWizard",
  "MemoryOverviewTab",
  "ActiveBlocksTab",
  "ImportTab",
  "ExportTab",
  "CostTab",
  "ModelControlTab",
  "PrivacyTab",
  "DiagnosticsTab",
  "SettingsTab",
] as const;

export type MemoristSurface = typeof MEMORIST_UI_SURFACES[number];

export function firstRunWizardSteps() {
  return [
    "Local-only confirmation",
    "Runtime check",
    "Workspace and project",
    "Configure Memorist model roles",
    "Memory mode",
    "Optional import",
    "Finish smoke check",
  ];
}

export function destructiveActionsRequireConfirmation(action: string): boolean {
  return ["import_commit", "forget_memory", "delete_session", "purge_project", "restore"].includes(action);
}

export function sanitizeUiText(value: string): string {
  return value.replace(/<script/gi, "&lt;script").replace(/onerror=/gi, "data-blocked=");
}
