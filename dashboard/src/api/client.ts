import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "./schema";

const TOKEN_KEY = "jarvis.token";

/** The token the operator pasted (Settings), sent with every request. Empty: anonymous. */
export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // private mode: the token lasts for this page only
  }
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

const auth: Middleware = {
  onRequest({ request }) {
    const token = getToken();
    if (token) request.headers.set("Authorization", `Bearer ${token}`);
    return request;
  },
};

export const client = createClient<paths>({
  baseUrl: globalThis.location?.origin ?? "http://localhost",
});
client.use(auth);

function detail(error: unknown): string | undefined {
  if (error && typeof error === "object" && "detail" in error) {
    const d = (error as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map((x) => (x as { msg?: string }).msg ?? String(x)).join("; ");
  }
  return undefined;
}

/** The body of a response, typed as the caller knows it; an ApiError with the gateway's reason. */
export async function unwrap<T>(
  call: Promise<{ data?: unknown; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await call;
  if (error !== undefined || !response.ok) {
    throw new ApiError(response.status, detail(error) ?? (response.statusText || `HTTP ${response.status}`));
  }
  return data as T;
}

/** ws(s)://host/api/v1/stream, with the token (browsers cannot set headers on a WebSocket). */
export function streamUrl(): string {
  const loc = globalThis.location;
  const proto = loc?.protocol === "https:" ? "wss:" : "ws:";
  const token = getToken();
  return `${proto}//${loc?.host ?? "localhost"}/api/v1/stream${token ? `?token=${encodeURIComponent(token)}` : ""}`;
}
