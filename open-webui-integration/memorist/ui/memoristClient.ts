import type { MemoristModelRole, ModelControlProfile } from "./modelControl";

export type MemoristMode = "off" | "lite" | "standard" | "full";
export type MemoristTurnPolicy = "full" | "no_recall" | "private";
export type MemoristRegenerationInstruction = {
  regeneration_uuid: string;
  original_input_message_uuid: string;
  original_user_prompt: string;
  turn_policy: "no_recall";
  create_attachment: false;
  duplicate_user_capture: false;
  original_attachment_audit_preserved: true;
  prompt_hash: string;
};
export type MemoristRegenerationRequestBody = {
  messages: Array<{ role: "user"; content: string }>;
  memorist: { turn_policy: "no_recall"; attachment_review: false };
  metadata: {
    memorist_regeneration_uuid: string;
    memorist_input_message_uuid: string;
  };
};
export const MEMORY_CONTROL_LABELS = {
  memoristRecall: "Memorist Recall",
  openWebUINativeMemory: "Open WebUI Native Memory",
} as const;

export type MemoristPolicyResolution = {
  policy: {
    mode: MemoristTurnPolicy;
    capture_enabled: boolean;
    recall_enabled: boolean;
    attachment_enabled: boolean;
    private: boolean;
  };
  source: "turn" | "chat" | "user" | "system";
  attachment_review: boolean;
  runtime_profile: "lite" | "full" | "dev";
  runtime_profile_server_controlled: true;
  openwebui_native_memory_independent: true;
};

export type MemoristHealth = {
  status: ImportRunStatus;
  service: string;
  local_only: boolean;
};

export type ModelControlProfileList = { items: ModelControlProfile[] };

export type ModelControlProfileCreate = {
  profile_name?: string | null;
  provider_type?: string;
  provider_name?: string | null;
  model_name?: string;
  role: MemoristModelRole;
  endpoint_url?: string | null;
  endpoint_is_local?: boolean | null;
  context_window?: number | null;
  max_input_tokens?: number | null;
  max_output_tokens?: number | null;
  supports_structured_output?: boolean;
  supports_json_mode?: boolean;
  supports_embeddings?: boolean;
  embedding_dimension?: number | null;
  tokenizer_family?: string | null;
  quality_profile?: string;
  latency_profile?: string;
  quality_profile_data?: Record<string, unknown> | null;
  latency_profile_data?: Record<string, unknown> | null;
  cost_profile?: Record<string, unknown> | null;
  privacy_profile?: Record<string, unknown> | null;
  metadata?: Record<string, unknown> | null;
  secret_strategy?: string;
  secret_env_var_name?: string | null;
  is_enabled?: boolean;
  privacy_acknowledged?: boolean;
};

export type ModelControlProfilePatch = Partial<ModelControlProfileCreate>;

export type ModelControlDefault = {
  role: MemoristModelRole;
  model_profile_uuid: string | null;
  workspace_uuid?: string | null;
  project_uuid?: string | null;
  created_at?: string;
};

export type ModelControlRoleDefaultSet = {
  role: MemoristModelRole;
  model_profile_uuid: string;
  workspace_uuid?: string | null;
  project_uuid?: string | null;
};

export type ModelControlRoleDefaultSetResponse = ModelControlRoleDefaultSet & {
  reindex_required: boolean;
};

export type ModelControlDefaultsResponse = { items: ModelControlDefault[] };

export type ModelControlHealthEvent = {
  model_profile_uuid: string;
  status: string;
  latency_ms?: number | null;
  detail_sanitized?: string | null;
};

export type ModelControlHealthResponse = {
  status: string;
  latest_health_events: ModelControlHealthEvent[];
};

export type ModelControlProviderHealth = {
  status: string;
  provider_type: string;
  model_name: string;
  latency_ms: number;
  local_only_safe: boolean;
  detail?: string | null;
};

export type ModelControlProfileTestRequest = {
  timeout_ms?: number;
};

export type ModelControlProfileTestResponse = {
  model_profile_uuid: string;
  health: ModelControlProviderHealth;
};

export type PrivacyAcknowledgementRequest = {
  model_profile_uuid: string;
  acknowledged_risk_level: string;
  acknowledged_data_sent: Record<string, unknown>;
};

export type PrivacyAcknowledgementResponse = {
  ack_uuid: string;
  model_profile_uuid: string;
  role: MemoristModelRole;
  acknowledged_risk_level: string;
  acknowledged_data_sent: Record<string, unknown>;
  acknowledged_at: string;
  created_at: string;
  schema_version: number;
};

export class MemoristClient {
  constructor(private readonly baseUrl: string = "/api/v1/memorist") {}

  async health(): Promise<MemoristHealth> {
    return this.get<MemoristHealth>("/health");
  }

  async config(): Promise<unknown> { return this.get("/config/effective"); }
  async sessions(): Promise<unknown> { return this.get("/openwebui/status"); }
  async messages(): Promise<unknown> { return this.get("/memories"); }
  async blocks(): Promise<unknown> { return this.get("/blocks/rebuild-stale"); }
  async imports(): Promise<unknown> { return this.get("/imports"); }
  async exports(): Promise<unknown> { return this.get("/heritage/inspect"); }
  async privacy(): Promise<unknown> { return this.get("/privacy/requests/latest"); }
  async costs(): Promise<unknown> { return this.get("/costs/model-roles"); }
  async modelControlRoles(): Promise<unknown> { return this.get("/model-control/roles"); }
  async modelControlProfiles(): Promise<ModelControlProfileList> { return this.get("/model-control/profiles"); }
  async modelControlDefaults(): Promise<ModelControlDefaultsResponse> { return this.get("/model-control/defaults"); }
  async modelControlUsage(): Promise<unknown> { return this.get("/model-control/usage"); }
  async modelControlPrivacy(): Promise<unknown> { return this.get("/model-control/privacy"); }
  async modelControlHealth(): Promise<ModelControlHealthResponse> { return this.get("/model-control/health"); }
  async createModelControlProfile(payload: ModelControlProfileCreate): Promise<ModelControlProfile> { return this.post("/model-control/profiles", payload); }
  async patchModelControlProfile(modelProfileUuid: string, payload: ModelControlProfilePatch): Promise<ModelControlProfile> { return this.patch(`/model-control/profiles/${encodeURIComponent(modelProfileUuid)}`, payload); }
  async testModelControlProfile(modelProfileUuid: string, payload: ModelControlProfileTestRequest = {}): Promise<ModelControlProfileTestResponse> { return this.post(`/model-control/profiles/${encodeURIComponent(modelProfileUuid)}/test`, payload); }
  async setModelControlDefault(payload: ModelControlRoleDefaultSet): Promise<ModelControlRoleDefaultSetResponse> { return this.post("/model-control/defaults", payload); }
  async acknowledgeModelControlPrivacy(payload: PrivacyAcknowledgementRequest): Promise<PrivacyAcknowledgementResponse> { return this.post("/model-control/privacy/acknowledge", payload); }
  async modelRoleCosts(): Promise<unknown> { return this.get("/costs/model-roles"); }
  async diagnostics(): Promise<unknown> { return this.get("/openwebui/status"); }
  async resolveMemoristPolicy(payload: {
    user_uuid?: string | null;
    chat_uuid?: string | null;
    workspace_uuid?: string | null;
    memorist?: { turn_policy?: MemoristTurnPolicy; attachment_review?: boolean };
  }): Promise<MemoristPolicyResolution> {
    return this.post("/memory-control/policy/resolve", payload);
  }
  async setMemoristDefault(payload: {
    scope_type: "user" | "chat";
    scope_uuid: string;
    workspace_uuid?: string | null;
    turn_policy: MemoristTurnPolicy;
    attachment_review?: boolean;
  }): Promise<unknown> {
    return this.put("/memory-control/policy/defaults", payload);
  }
  async previewAttachment(attachmentUuid: string): Promise<unknown> {
    return this.get(`/memory-control/attachments/${encodeURIComponent(attachmentUuid)}/preview`);
  }
  async fetchAttachmentSources(attachmentUuid: string): Promise<unknown> {
    return this.get(`/memory-control/attachments/${encodeURIComponent(attachmentUuid)}/sources`);
  }
  async approveAttachment(attachmentUuid: string, idempotencyKey: string): Promise<unknown> {
    return this.post(`/memory-control/attachments/${encodeURIComponent(attachmentUuid)}/approve`, { idempotency_key: idempotencyKey });
  }
  async suppressAttachment(attachmentUuid: string, idempotencyKey: string): Promise<unknown> {
    return this.post(`/memory-control/attachments/${encodeURIComponent(attachmentUuid)}/suppress`, { idempotency_key: idempotencyKey });
  }
  async cancelAttachmentBeforeSend(attachmentUuid: string, idempotencyKey: string): Promise<unknown> {
    return this.post(`/memory-control/attachments/${encodeURIComponent(attachmentUuid)}/cancel`, { idempotency_key: idempotencyKey });
  }
  async recordAttachmentDelivery(attachmentUuid: string, idempotencyKey: string, responseMessageUuid?: string): Promise<unknown> {
    return this.post(`/memory-control/attachments/${encodeURIComponent(attachmentUuid)}/delivery`, { idempotency_key: idempotencyKey, response_message_uuid: responseMessageUuid });
  }
  async recordAttachmentRejection(attachmentUuid: string, idempotencyKey: string): Promise<unknown> {
    return this.post(`/memory-control/attachments/${encodeURIComponent(attachmentUuid)}/rejection`, { idempotency_key: idempotencyKey });
  }
  async regenerateWithoutRecall(attachmentUuid: string, sourceResponseMessageUuid: string, idempotencyKey: string): Promise<MemoristRegenerationInstruction> {
    return this.post<MemoristRegenerationInstruction>(`/memory-control/attachments/${encodeURIComponent(attachmentUuid)}/regenerate-without-recall`, {
      idempotency_key: idempotencyKey,
      response_message_uuid: sourceResponseMessageUuid,
    });
  }
  buildRegenerationRequest(instruction: MemoristRegenerationInstruction): MemoristRegenerationRequestBody {
    return {
      messages: [{ role: "user", content: instruction.original_user_prompt }],
      memorist: { turn_policy: "no_recall", attachment_review: false },
      metadata: {
        memorist_regeneration_uuid: instruction.regeneration_uuid,
        memorist_input_message_uuid: instruction.original_input_message_uuid,
      },
    };
  }

  private async get<T = unknown>(path: string, headers?: Record<string, string>): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, { credentials: "same-origin", headers });
    if (!response.ok) throw new Error(await this.errorDetail(response));
    return response.json() as Promise<T>;
  }

  private async post<T = unknown>(path: string, body: unknown, headers?: Record<string, string>): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...headers },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(await this.errorDetail(response));
    return response.json() as Promise<T>;
  }

  private async patch<T = unknown>(path: string, body: unknown): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      method: "PATCH",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(await this.errorDetail(response));
    return response.json() as Promise<T>;
  }

  private async put<T = unknown>(path: string, body: unknown, headers?: Record<string, string>): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      method: "PUT",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...headers },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(await this.errorDetail(response));
    return response.json() as Promise<T>;
  }

  private async errorDetail(response: Response): Promise<string> {
    try {
      const payload = await response.json();
      const detail = payload?.detail ?? payload;
      return typeof detail === "string"
        ? detail
        : JSON.stringify(detail);
    } catch {
      return `Memorist request failed: ${response.status}`;
    }
  }

}


export type ImportRunStatus =
  | "staged" | "uploaded" | "inspected" | "reconstructed" | "dry_run" | "committing"
  | "processing" | "paused" | "committed" | "fully_reconstructed" | "completed_with_failures"
  | "completed" | "cancelled" | "failed";

export type ImportProcessingModel = {
  role?: string | null;
  model_profile_uuid?: string | null;
  provider_type?: string | null;
  model_name?: string | null;
  deterministic_fallback?: boolean | null;
};

export type ImportExpectedRows = { sessions?: number; messages?: number; import_mappings?: number };

export type ImportDryRunReport = {
  import_run_uuid: string;
  plan_fingerprint: string;
  created_at: string;
  source_platform?: string | null;
  source_platform_display?: string | null;
  detected_format?: string | null;
  conversation_count?: number | null;
  message_count?: number | null;
  eligible_processing_message_count?: number | null;
  ineligible_processing_message_count?: number | null;
  skip_reasons?: Record<string, number>;
  duplicate_conversation_count?: number | null;
  duplicate_message_count?: number | null;
  expected_new_database_rows?: ImportExpectedRows | null;
  expected_text_unitization_jobs?: number | null;
  expected_memory_processing_jobs?: number | null;
  processing_mode?: string | null;
  processing_model?: ImportProcessingModel | null;
  deterministic_fallback?: boolean | null;
  graph_projection_enabled?: boolean | null;
  large_reconstruction_warning?: string | null;
};

export type ImportProgress = {
  import_run_uuid: string;
  status: ImportRunStatus;
  phase?: string;
  records_total?: number;
  records_done?: number;
  error_count?: number;
};

export type ImportProcessingReport = {
  import_run_uuid: string;
  final_status: string;
  terminal: boolean;
  last_error_sanitized: string | null;
  imported_conversations: number; imported_messages: number; processing_jobs_total: number;
  processing_jobs_queued: number; processing_jobs_pending: number; processing_jobs_running: number;
  processing_jobs_retry_scheduled: number; processing_jobs_succeeded: number;
  processing_jobs_failed: number; processing_jobs_skipped: number;
  processing_jobs_already_processed: number; memory_candidates_created: number;
  memory_versions_created: number; prompt_execution_runs: number; model_usage_events: number;
  input_tokens: number; output_tokens: number; graph_projection_outbox_events: number;
  skip_reasons: Record<string, number>;
  provider_breakdown: Array<{
    operational_role: string; prompt_role?: string | null; model_profile_uuid?: string | null;
    provider_type: string; model_name: string; processing_jobs: number;
    input_tokens: number; output_tokens: number; succeeded: number; failed: number;
  }>;
};

export type ImportUploadOptions = {
  mode?: string;
  processing_mode?: string;
  target_workspace_uuid?: string;
  target_project_uuid?: string;
};

export type ImportRunSummary = {
  import_run_uuid: string;
  status: ImportRunStatus;
  source_platform?: string | null;
  detected_format?: string | null;
  total_conversations?: number;
  total_messages?: number;
  imported_conversations?: number;
  imported_messages?: number;
  skipped_records?: number;
  warning_count?: number;
  error_count?: number;
  created_at?: string;
  completed_at?: string | null;
};

export type RecentImportRunsResponse = { items: ImportRunSummary[] };

export async function uploadImportFile(
  baseUrl: string,
  file: File,
  options: ImportUploadOptions = {},
  onProgress?: (uploadedBytes: number, totalBytes: number) => void,
): Promise<ImportRunSummary> {
  const form = new FormData();
  form.append("file", file);
  for (const [key, value] of Object.entries(options)) {
    if (value !== undefined && value !== null && value !== "") form.append(key, String(value));
  }
  if (!onProgress || typeof XMLHttpRequest === "undefined") {
    const response = await fetch(`${baseUrl}/imports/upload-file`, {
      method: "POST",
      credentials: "same-origin",
      body: form,
    });
    if (!response.ok) throw new Error(await safeImportError(response));
    return response.json() as Promise<ImportRunSummary>;
  }
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", `${baseUrl}/imports/upload-file`);
    request.withCredentials = true;
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded, event.total);
    };
    request.onload = () => {
      try {
        const payload = JSON.parse(request.responseText || "{}");
        if (request.status >= 200 && request.status < 300) resolve(payload);
        else reject(new Error(typeof payload.detail === "string" ? payload.detail : "Import upload failed."));
      } catch {
        reject(new Error("Import upload failed."));
      }
    };
    request.onerror = () => reject(new Error("Import upload failed."));
    request.send(form);
  });
}

async function safeImportError(response: Response): Promise<string> {
  try {
    const payload = await response.json();
    return typeof payload?.detail === "string" ? payload.detail : "Import request failed.";
  } catch {
    return "Import request failed.";
  }
}
