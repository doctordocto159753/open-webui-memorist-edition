const CONTROL_PREFIX = "/api/v1/memorist";
const LEGACY_IMPORT_PREFIX = "/memcore/imports";
const CONTROL_IMPORT_PREFIX = `${CONTROL_PREFIX}/imports`;
const INSTALL_FLAG = "__memoristAuthenticatedTransportInstalled" as const;

type MemoristWindow = Window & {
  __memoristAuthenticatedTransportInstalled?: boolean;
};

function browserToken(): string {
  if (typeof window === "undefined" || typeof window.localStorage === "undefined") return "";
  return window.localStorage.getItem("token") || "";
}

function sameOriginPath(value: string): { url: URL; relative: boolean } | null {
  if (typeof window === "undefined") return null;
  try {
    const relative = value.startsWith("/");
    const url = new URL(value, window.location.origin);
    return url.origin === window.location.origin ? { url, relative } : null;
  } catch {
    return null;
  }
}

export function memoristControlUrl(value: string): string {
  const parsed = sameOriginPath(value);
  if (!parsed) return value;
  if (
    parsed.url.pathname === LEGACY_IMPORT_PREFIX ||
    parsed.url.pathname.startsWith(`${LEGACY_IMPORT_PREFIX}/`)
  ) {
    parsed.url.pathname = `${CONTROL_IMPORT_PREFIX}${parsed.url.pathname.slice(LEGACY_IMPORT_PREFIX.length)}`;
    return parsed.relative
      ? `${parsed.url.pathname}${parsed.url.search}${parsed.url.hash}`
      : parsed.url.toString();
  }
  return value;
}

export function isMemoristAuthenticatedUrl(value: string): boolean {
  const parsed = sameOriginPath(memoristControlUrl(value));
  return Boolean(
    parsed &&
      (parsed.url.pathname === CONTROL_PREFIX ||
        parsed.url.pathname.startsWith(`${CONTROL_PREFIX}/`)),
  );
}

function authenticatedHeaders(initial?: HeadersInit): Headers {
  const headers = new Headers(initial || {});
  const token = browserToken();
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  return headers;
}

function rewrittenRequest(input: Request, targetUrl: string, init: RequestInit | undefined): Request {
  const method = init?.method || input.method;
  const base: RequestInit = {
    method,
    body: method === "GET" || method === "HEAD" ? undefined : input.clone().body,
    mode: input.mode,
    credentials: input.credentials,
    cache: input.cache,
    redirect: input.redirect,
    referrer: input.referrer,
    referrerPolicy: input.referrerPolicy,
    integrity: input.integrity,
    keepalive: input.keepalive,
    signal: input.signal,
  };
  return new Request(targetUrl, { ...base, ...init, headers: authenticatedHeaders(init?.headers || input.headers) });
}

export function installMemoristAuthenticatedTransport(): void {
  if (typeof window === "undefined" || typeof window.fetch !== "function") return;
  const host = window as MemoristWindow;
  if (host[INSTALL_FLAG]) return;
  host[INSTALL_FLAG] = true;

  const nativeFetch = window.fetch.bind(window);
  window.fetch = (async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const rawUrl =
      typeof input === "string" || input instanceof URL ? input.toString() : input.url;
    const targetUrl = memoristControlUrl(rawUrl);
    if (!isMemoristAuthenticatedUrl(targetUrl)) return nativeFetch(input, init);

    if (input instanceof Request) {
      return nativeFetch(rewrittenRequest(input, targetUrl, init));
    }
    return nativeFetch(targetUrl, {
      ...init,
      headers: authenticatedHeaders(init?.headers),
    });
  }) as typeof window.fetch;

  if (typeof XMLHttpRequest === "undefined") return;
  const nativeOpen = XMLHttpRequest.prototype.open;
  const nativeSend = XMLHttpRequest.prototype.send;
  const authenticated = new WeakSet<XMLHttpRequest>();

  XMLHttpRequest.prototype.open = (function (
    this: XMLHttpRequest,
    method: string,
    url: string | URL,
    async = true,
    username?: string | null,
    password?: string | null,
  ): void {
    const target = memoristControlUrl(url.toString());
    if (isMemoristAuthenticatedUrl(target)) authenticated.add(this);
    (nativeOpen as unknown as (...args: unknown[]) => void).call(
      this,
      method,
      target,
      async,
      username,
      password,
    );
  }) as typeof XMLHttpRequest.prototype.open;

  XMLHttpRequest.prototype.send = function (
    body?: Document | XMLHttpRequestBodyInit | null,
  ): void {
    if (authenticated.has(this)) {
      const token = browserToken();
      if (token) this.setRequestHeader("Authorization", `Bearer ${token}`);
    }
    nativeSend.call(this, body ?? null);
  };
}

installMemoristAuthenticatedTransport();
