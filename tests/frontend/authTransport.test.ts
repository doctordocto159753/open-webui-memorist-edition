import { afterEach, describe, expect, it, vi } from "vitest";

const installFlag = "__memoristAuthenticatedTransportInstalled";

type FetchCall = { input: RequestInfo | URL; init?: RequestInit };

function headerValue(init: RequestInit | undefined, name: string): string | null {
  return new Headers(init?.headers).get(name);
}

describe("Memorist authenticated browser transport", () => {
  const originalFetch = window.fetch;
  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;

  afterEach(() => {
    window.fetch = originalFetch;
    XMLHttpRequest.prototype.open = originalOpen;
    XMLHttpRequest.prototype.send = originalSend;
    localStorage.clear();
    delete (window as unknown as Record<string, unknown>)[installFlag];
    vi.restoreAllMocks();
    vi.resetModules();
  });

  it("adds the Open WebUI bearer token and rewrites only same-origin import calls", async () => {
    localStorage.setItem("token", "openwebui-jwt-test-token");
    const calls: FetchCall[] = [];
    const nativeFetch: typeof window.fetch = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ input, init });
        return new Response("{}", { status: 200 });
      },
    );
    window.fetch = nativeFetch;

    const transport = await import(
      "../../open-webui-integration/memorist/ui/authTransport"
    );

    await window.fetch("/api/v1/memorist/openwebui/status");
    expect(calls[0]?.input).toBe("/api/v1/memorist/openwebui/status");
    expect(headerValue(calls[0]?.init, "Authorization")).toBe(
      "Bearer openwebui-jwt-test-token",
    );

    await window.fetch("/memcore/imports?limit=10");
    expect(calls[1]?.input).toBe("/api/v1/memorist/imports?limit=10");
    expect(headerValue(calls[1]?.init, "Authorization")).toBe(
      "Bearer openwebui-jwt-test-token",
    );

    await window.fetch("https://provider.example/v1/models");
    expect(calls[2]?.input).toBe("https://provider.example/v1/models");
    expect(headerValue(calls[2]?.init, "Authorization")).toBeNull();

    expect(transport.memoristControlUrl("/memcore/imports/run-1/progress")).toBe(
      "/api/v1/memorist/imports/run-1/progress",
    );
    expect(
      transport.isMemoristAuthenticatedUrl("https://provider.example/api/v1/memorist/x"),
    ).toBe(false);
  });
});
