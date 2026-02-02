const API_BASE = import.meta.env?.VITE_API_URL ?? "http://localhost:8000";

export { API_BASE };

export async function api<T>(
  path: string,
  opts?: { method?: string; body?: unknown }
): Promise<T> {
  const url = `${API_BASE}${path}`;
  // Default to POST if body is provided, otherwise use specified method or GET
  const method = opts?.body ? (opts?.method || "POST") : (opts?.method || "GET");
  console.log(`API call: ${method} ${url}`, opts?.body ? { body: opts.body } : "");
  let res: Response;
  try {
    res = await fetch(url, {
      method: method,
      headers: opts?.body ? { "Content-Type": "application/json" } : undefined,
      body: opts?.body ? JSON.stringify(opts.body) : undefined,
    });
  } catch (e) {
    const err = e as Error;
    console.error(`API request failed: ${opts?.method ?? "GET"} ${url}`, err);
    if (err.name === "TypeError" && err.message.includes("fetch")) {
      const errorMsg = err.message || "Unknown network error";
      throw new Error(
        `Cannot reach API at ${API_BASE}. Is the server running on port 8000? Error: ${errorMsg}. Check browser console for details.`
      );
    }
    throw e;
  }
  const text = await res.text();
  if (!res.ok) {
    let msg: string = text || `HTTP ${res.status}`;
    try {
      const j = JSON.parse(text) as {
        detail?: { message?: string; retry_after_seconds?: number } | string;
      };
      const d = j.detail;
      msg = (typeof d === "object" && d?.message ? d.message : typeof d === "string" ? d : text) || msg;
      if (res.status === 429 && typeof d === "object" && typeof d?.retry_after_seconds === "number") {
        msg = msg || `Too many requests. Retry after ${d.retry_after_seconds} seconds.`;
      }
    } catch {
      /* use msg */
    }
    throw new Error(msg);
  }
  return (text ? JSON.parse(text) : {}) as T;
}

export type Tenant = { id: string; name: string; callback_url: string | null; created_at: string };
export type TenantStatus = {
  authorized: boolean;
  phone: string | null;
  last_error: string | null;
  cooldown_seconds: number;
};
export type SendMessageRes = { ok: boolean; peer_resolved: string; message_id: number; date: string };

export type CallbackPayloadEntry = { received_at: string; payload: Record<string, unknown> };

export async function apiDevCallbackPayloads(): Promise<
  { enabled: false } | { enabled: true; payloads: CallbackPayloadEntry[] }
> {
  const res = await fetch(`${API_BASE}/dev/callback-receiver`);
  if (res.status === 404) return { enabled: false };
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const payloads = (await res.json()) as CallbackPayloadEntry[];
  return { enabled: true, payloads };
}

export async function checkApiConnection(): Promise<{ connected: boolean; error?: string }> {
  try {
    const res = await fetch(`${API_BASE}/health`, { method: "GET" });
    if (res.ok) {
      return { connected: true };
    }
    return { connected: false, error: `HTTP ${res.status}` };
  } catch (e) {
    const err = e as Error;
    return { connected: false, error: err.message };
  }
}
