/**
 * Typed client for the FastAPI backend. Plain fetch on purpose -- there is no
 * cross-component cache to justify a query library yet.
 */

export type ProbeStatus = "ok" | "degraded" | "unavailable";

export interface Probe {
  name: string;
  status: ProbeStatus;
  detail: string;
  fallback: string;
  meta: Record<string, unknown>;
}

export interface HealthReport {
  status: ProbeStatus;
  provider: string;
  probes: Probe[];
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      detail = ((await resp.json()) as { detail?: string }).detail ?? detail;
    } catch {
      /* body was not JSON; the status text will do */
    }
    throw new ApiError(detail, resp.status);
  }
  return (await resp.json()) as T;
}

export const api = {
  health: () => request<HealthReport>("/api/health"),
  reloadConfig: () => request<HealthReport>("/api/health/reload", { method: "POST" }),
};
