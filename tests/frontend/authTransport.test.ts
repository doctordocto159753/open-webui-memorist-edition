import { afterEach, describe, expect, it, vi } from "vitest";

const installFlag = "__memoristAuthenticatedTransportInstalled";

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
    delete (window as Window & Record<string, unknown>)[installFlag];
    vi.restoreAllMocks();
    vi.resetModules();
  });

  it("adds the Open WebUI bearer token and rewrites only same-origin import calls", async () => {
    localStorage.setItem("token", "openwebui-jwt-test-token");
    const nativeFetch = vi.fn(async () => new Response("{}", { status: 200 }));
    window.fetch = nativeFetch as typeof window.fetch;

    const transport = await import(
      "../../open-webui-integration/memorist/ui/authTransport"
    );

    await window.fetch("/api/v1/memorist/openwebui/status");
    expect(nativeFetch).toHaveBeenNthCalledWith(
      1,
      "/api/v1/memorist/openwebui/status",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
    expect(headerValue(nativeFetch.mock.calls[0]?.[1], "Authorization")).toBe(
      "Bearer openwebui-jwt-test-token",
    );

    await window.fetch("/memcore/imports?limit=10");
    expect(nativeFetch.mock.calls[1]?.[0]).toBe("/api/v1/memorist/imports?limit=10");
    expect(headerValue(nativeFetch.mock.calls[1]?.[1], "Authorization")).toBe(
      "Bearer openwebui-jwt-test-token",
    );

    await window.fetch("https://provider.example/v1/models");
    expect(nativeFetch.mock.calls[2]?.[0]).toBe("https://provider.example/v1/models");
    expect(headerValue(nativeFetch.mock.calls[2]?.[1], "Authorization")).toBeNull();

    expect(transport.memoristControlUrl("/memcore/imports/run-1/progress")).toBe(
      "/api/v1/memorist/imports/run-1/progress",
    );
    expect(
      transport.isMemoristAuthenticatedUrl("https://provider.example/api/v1/memorist/x"),
    ).toBe(false);
  });
});
