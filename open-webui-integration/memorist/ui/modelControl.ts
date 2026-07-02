export const MEMORIST_MODEL_ROLES = [
  "main_chat_observed",
  "preflight",
  "memory_extraction",
  "embedding",
  "import_reconstruction",
  "high_confidence_extraction",
  "block_compaction",
  "privacy_sensitivity",
] as const;

export type MemoristModelRole = typeof MEMORIST_MODEL_ROLES[number];

export type ModelControlProfile = {
  model_profile_uuid: string;
  profile_name?: string | null;
  role: MemoristModelRole;
  provider_type: string;
  model_name: string;
  endpoint_is_local: boolean;
  cost_profile?: Record<string, unknown>;
  latency_profile_data?: Record<string, unknown>;
  quality_profile_data?: Record<string, unknown>;
  privacy_profile?: Record<string, unknown>;
  privacy_acknowledged_at?: string | null;
  secret_configured: boolean;
};

export const MODEL_CONTROL_TABS = [
  "Overview",
  "Main Chat",
  "Pre-flight",
  "Memory Extraction",
  "Embedding",
  "Cost & Usage",
  "Privacy",
  "Health",
] as const;

export const ROLE_MATRIX_COLUMNS = [
  "Role",
  "Provider",
  "Model",
  "Local/Remote",
  "Cost",
  "Latency",
  "Quality",
  "Privacy",
  "Health",
  "Default",
  "Actions",
] as const;

export const FIRST_RUN_MODEL_DEFAULTS = {
  main_chat_observed: "Selected in Open WebUI; Memorist observes only.",
  preflight: "deterministic_preflight, bounded and fail-open.",
  memory_extraction: "deterministic_extraction by default; local structured model recommended.",
  embedding: "disabled in Lite; local embedding model recommended for semantic search.",
} as const;

export function rolePrivacyBadge(profile: Pick<ModelControlProfile, "endpoint_is_local" | "privacy_acknowledged_at">): string {
  if (profile.endpoint_is_local) return "local-safe";
  return profile.privacy_acknowledged_at ? "remote-acknowledged" : "remote-needs-ack";
}

export function roleHelpText(role: MemoristModelRole): string {
  const help: Record<MemoristModelRole, string> = {
    main_chat_observed: "Observed from Open WebUI; Memorist does not control this model.",
    preflight: "Optional bounded pre-main-request role; must fail open.",
    memory_extraction: "Async post-response extraction role; never blocks chat.",
    embedding: "Separate semantic indexing role; profile changes can require re-indexing.",
    import_reconstruction: "Optional import repair role for incomplete local archives.",
    high_confidence_extraction: "Optional stricter extraction pass for high-value evidence.",
    block_compaction: "Optional background role for Active Memory Block compaction.",
    privacy_sensitivity: "Optional pre-storage sensitivity classifier for memory candidates.",
  };
  return help[role];
}
