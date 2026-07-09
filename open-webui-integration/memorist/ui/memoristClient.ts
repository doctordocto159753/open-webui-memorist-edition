import type { MemoristModelRole, ModelControlProfile } from "./modelControl";

export type MemoristMode = "off" | "lite" | "standard" | "full";

export type MemoristHealth = {
  status: string;
  service: string;
  local_only: boolean;
};

export type ModelControlProfileList = { items: ModelControlProfile[] };

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

export type PrivacyAcknowledgementRequest = {
  model_profile_uuid: string;
  acknowledged_risk_level: string;
  acknowledged_data_sent: Record<string, unknown>;
};

export class MemoristClient {
  constructor(private readonly baseUrl: string = "/memcore") {}

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
  async createModelControlProfile(payload: Partial<ModelControlProfile> & Record<string, unknown>): Promise<ModelControlProfile> { return this.post("/model-control/profiles", payload); }
  async patchModelControlProfile(modelProfileUuid: string, payload: Partial<ModelControlProfile> & Record<string, unknown>): Promise<ModelControlProfile> { return this.patch(`/model-control/profiles/${encodeURIComponent(modelProfileUuid)}`, payload); }
  async testModelControlProfile(modelProfileUuid: string, payload: { timeout_ms?: number } = {}): Promise<unknown> { return this.post(`/model-control/profiles/${encodeURIComponent(modelProfileUuid)}/test`, payload); }
  async setModelControlDefault(payload: ModelControlRoleDefaultSet): Promise<unknown> { return this.post("/model-control/defaults", payload); }
  async acknowledgeModelControlPrivacy(payload: PrivacyAcknowledgementRequest): Promise<unknown> { return this.post("/model-control/privacy/acknowledge", payload); }
  async modelRoleCosts(): Promise<unknown> { return this.get("/costs/model-roles"); }
  async diagnostics(): Promise<unknown> { return this.get("/openwebui/status"); }

  private async get<T = unknown>(path: string): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, { credentials: "same-origin" });
    if (!response.ok) throw new Error(await this.errorDetail(response));
    return response.json() as Promise<T>;
  }

  private async post<T = unknown>(path: string, body: unknown): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
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

  private async errorDetail(response: Response): Promise<string> {
    try {
      const payload = await response.json();
      return `Memorist request failed: ${response.status} ${payload.detail || JSON.stringify(payload)}`;
    } catch {
      return `Memorist request failed: ${response.status}`;
    }
  }
}
