export type MemoristMode = "off" | "lite" | "standard" | "full";

export type MemoristHealth = {
  status: string;
  service: string;
  local_only: boolean;
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
  async modelControlProfiles(): Promise<unknown> { return this.get("/model-control/profiles"); }
  async modelControlDefaults(): Promise<unknown> { return this.get("/model-control/defaults"); }
  async modelControlUsage(): Promise<unknown> { return this.get("/model-control/usage"); }
  async modelControlPrivacy(): Promise<unknown> { return this.get("/model-control/privacy"); }
  async modelControlHealth(): Promise<unknown> { return this.get("/model-control/health"); }
  async modelRoleCosts(): Promise<unknown> { return this.get("/costs/model-roles"); }
  async diagnostics(): Promise<unknown> { return this.get("/openwebui/status"); }

  private async get<T = unknown>(path: string): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, { credentials: "same-origin" });
    if (!response.ok) throw new Error(`Memorist request failed: ${response.status}`);
    return response.json() as Promise<T>;
  }
}
