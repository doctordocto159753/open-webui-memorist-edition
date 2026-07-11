import "./processingNodes";
import "./importWorkflow";

import {
  MEMORIST_IMPORT_ROUTE,
  MEMORIST_IMPORT_RUN_ROUTE,
} from "./importWorkflow";
import { MEMORIST_PROCESSING_NODES_ROUTE } from "./processingNodes";

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
  "MemoristProcessingNodesSettings",
  "MemoristImportWorkflow",
] as const;

export type MemoristSurface = typeof MEMORIST_UI_SURFACES[number];

export type MemoristSettingsNavigationItem = {
  label: string;
  href: string;
  surface: MemoristSurface;
  adminOnly: boolean;
};

export type MemoristSettingsRoute = {
  path: string;
  surface: MemoristSurface;
  element: string;
};

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

export const MEMORIST_SETTINGS_ROUTES = {
  processingNodes: MEMORIST_PROCESSING_NODES_ROUTE,
  import: MEMORIST_IMPORT_ROUTE,
  importRun: MEMORIST_IMPORT_RUN_ROUTE,
} as const;

export const MEMORIST_SETTINGS_NAVIGATION: readonly MemoristSettingsNavigationItem[] = [
  {
    label: "Import",
    href: MEMORIST_SETTINGS_ROUTES.import,
    surface: "MemoristImportWorkflow",
    adminOnly: false,
  },
  {
    label: "Processing Nodes",
    href: MEMORIST_SETTINGS_ROUTES.processingNodes,
    surface: "MemoristProcessingNodesSettings",
    adminOnly: true,
  },
] as const;

export const MEMORIST_SETTINGS_ROUTE_MOUNTS: readonly MemoristSettingsRoute[] = [
  {
    path: MEMORIST_SETTINGS_ROUTES.import,
    surface: "MemoristImportWorkflow",
    element: "memorist-import-workflow",
  },
  {
    path: MEMORIST_SETTINGS_ROUTES.importRun,
    surface: "MemoristImportWorkflow",
    element: "memorist-import-workflow",
  },
  {
    path: MEMORIST_SETTINGS_ROUTES.processingNodes,
    surface: "MemoristProcessingNodesSettings",
    element: "memorist-processing-nodes-settings",
  },
] as const;

export function renderMemoristSettingsPanel(path: string): HTMLElement | undefined {
  const route = MEMORIST_SETTINGS_ROUTE_MOUNTS.find((candidate) => {
    if (candidate.path === path) return true;
    return (
      candidate.path === MEMORIST_SETTINGS_ROUTES.importRun
      && path.startsWith(`${MEMORIST_SETTINGS_ROUTES.import}/`)
    );
  });
  if (!route || typeof document === "undefined") return undefined;
  return document.createElement(route.element);
}
