import "./processingNodes";
import "./memoryNodeSetup";
import "./importWorkflow";
import "./memoryAttachment";
import "./memoryWorkflowToggle";
import "./diagnostics";
import "./messageDisclosure";

import { MEMORIST_DIAGNOSTICS_ROUTE } from "./diagnostics";
import { MEMORIST_MEMORY_NODE_SETUP_ROUTE } from "./memoryNodeSetup";
import { MEMORIST_PROCESSING_NODES_ROUTE } from "./processingNodes";

/**
 * Surfaces that are implemented, mounted in the shipped Open WebUI frontend,
 * and covered by integration tests. This list is a release claim: adding a
 * name here without a mounted production component is a release defect.
 * Former aspirational entries were removed in PR5-G (see ui/README.md) and
 * remain future work; they must not be re-added until actually mounted.
 */
export const MEMORIST_UI_SURFACES = [
  "MemoryNodeSetup",
  "MemoristProcessingNodesSettings",
  "MemoristDiagnostics",
  "ImportTab",
  "MemoryAttachment",
  "MemoryWorkflowToggle",
  "MemoristMessageDisclosure",
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

export function destructiveActionsRequireConfirmation(action: string): boolean {
  return ["import_commit", "forget_memory", "delete_session", "purge_project", "restore"].includes(action);
}

export function sanitizeUiText(value: string): string {
  return value.replace(/<script/gi, "&lt;script").replace(/onerror=/gi, "data-blocked=");
}

export const MEMORIST_SETTINGS_ROUTES = {
  memorySetup: MEMORIST_MEMORY_NODE_SETUP_ROUTE,
  processingNodes: MEMORIST_PROCESSING_NODES_ROUTE,
  diagnostics: MEMORIST_DIAGNOSTICS_ROUTE,
  importWorkflow: "/settings/memorist/import",
} as const;

export const MEMORIST_SETTINGS_NAVIGATION: readonly MemoristSettingsNavigationItem[] = [
  {
    label: "Memory Setup",
    href: MEMORIST_SETTINGS_ROUTES.memorySetup,
    surface: "MemoryNodeSetup",
    adminOnly: true,
  },
  {
    label: "Processing Nodes",
    href: MEMORIST_SETTINGS_ROUTES.processingNodes,
    surface: "MemoristProcessingNodesSettings",
    adminOnly: true,
  },
  {
    label: "Diagnostics",
    href: MEMORIST_SETTINGS_ROUTES.diagnostics,
    surface: "MemoristDiagnostics",
    adminOnly: true,
  },
  {
    label: "Import",
    href: MEMORIST_SETTINGS_ROUTES.importWorkflow,
    surface: "ImportTab",
    adminOnly: true,
  },
] as const;

export const MEMORIST_SETTINGS_ROUTE_MOUNTS: readonly MemoristSettingsRoute[] = [
  {
    path: MEMORIST_SETTINGS_ROUTES.memorySetup,
    surface: "MemoryNodeSetup",
    element: "memorist-memory-node-setup",
  },
  {
    path: MEMORIST_SETTINGS_ROUTES.processingNodes,
    surface: "MemoristProcessingNodesSettings",
    element: "memorist-processing-nodes-settings",
  },
  {
    path: MEMORIST_SETTINGS_ROUTES.diagnostics,
    surface: "MemoristDiagnostics",
    element: "memorist-diagnostics",
  },
  {
    path: MEMORIST_SETTINGS_ROUTES.importWorkflow,
    surface: "ImportTab",
    element: "memorist-import-workflow",
  },
] as const;

export function renderMemoristSettingsPanel(path: string): HTMLElement | undefined {
  const route = MEMORIST_SETTINGS_ROUTE_MOUNTS.find((candidate) => candidate.path === path || (candidate.path === MEMORIST_SETTINGS_ROUTES.importWorkflow && path.startsWith(`${candidate.path}/`)));
  if (!route || typeof document === "undefined") return undefined;
  return document.createElement(route.element);
}

export { mountMemoryAttachmentForMessage } from "./memoryAttachment";
export {
  applyMemoryWorkflowToRequest,
  applyOriginalMemoryWorkflowToRegeneration,
  mountMemoryWorkflowToggleNearComposer,
} from "./memoryWorkflowToggle";

export { mountMemoryNodeSetup } from "./memoryNodeSetup";
export { mountMemoristDiagnostics } from "./diagnostics";
export { mountMessageDisclosure } from "./messageDisclosure";
