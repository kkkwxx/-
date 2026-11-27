import type { components } from "./schema";

export type Schemas = components["schemas"];

export class ApiError extends Error {
  status: number;
  code: string;
  detail?: unknown;

  constructor(status: number, code: string, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

function formatErrorDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (item && typeof item === "object") {
          const obj = item as Record<string, unknown>;
          const loc = Array.isArray(obj.loc) ? obj.loc.slice(1).join(".") : "";
          const msg = typeof obj.msg === "string" ? obj.msg : JSON.stringify(obj);
          return loc ? `${loc}: ${msg}` : msg;
        }
        return String(item);
      })
      .join("；");
  }
  if (detail && typeof detail === "object") {
    return JSON.stringify(detail);
  }
  return "";
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit & { auth?: boolean } = {}
): Promise<T> {
  const { auth = true, headers, ...rest } = options;
  const finalHeaders: Record<string, string> = {
    ...(rest.body ? { "Content-Type": "application/json" } : {}),
    ...(headers as Record<string, string> | undefined),
  };
  const resp = await fetch(path, {
    ...rest,
    headers: finalHeaders,
    credentials: auth ? "include" : "same-origin",
  });
  if (resp.status === 204) {
    return undefined as T;
  }
  const text = await resp.text();
  let parsed: unknown = {};
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = { message: text };
    }
  }
  const data: Record<string, unknown> =
    parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : { data: parsed };

  if (!resp.ok) {
    const code = String(data.code || `HTTP_${resp.status}`);
    const message =
      (typeof data.message === "string" && data.message) ||
      formatErrorDetail(data.detail) ||
      resp.statusText ||
      `请求失败 (${resp.status})`;
    throw new ApiError(resp.status, code, message, data.detail);
  }
  return parsed as T;
}

export function buildQuery(
  params: Record<string, string | number | boolean | undefined | null>
): string {
  const sp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") {
      sp.set(k, String(v));
    }
  });
  const qs = sp.toString();
  return qs ? `?${qs}` : "";
}
