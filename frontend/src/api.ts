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

// --- analysis ---------------------------------------------------------------

export type MatchStatus = "present_exact" | "present_as_synonym" | "implied" | "missing";

export interface WeightBreakdown {
  frequency: number;
  frequency_factor: number;
  section: string;
  section_factor: number;
  requirement: string;
  requirement_factor: number;
  distinct_sections: number;
  spread_factor: number;
  dictionary_factor: number;
  weight: number;
}

export interface RankedTerm {
  term: string;
  category: string;
  frequency: number;
  sections: string[];
  surfaces: string[];
  weight: number;
  weight_breakdown: WeightBreakdown;
  weight_formula: string;
  status: MatchStatus;
  location: "bullet" | "skills" | "other" | null;
  evidence: string;
  bullet_id: string | null;
  match_score: number;
}

export interface CategoryCoverage {
  category: string;
  covered_weight: number;
  total_weight: number;
  term_count: number;
  percent: number;
}

export interface Coverage {
  percent: number;
  covered_weight: number;
  total_weight: number;
  by_category: CategoryCoverage[];
  counts: Record<string, number>;
  credit_scheme: Record<string, number>;
  disclaimer: string;
}

export interface ParsedJD {
  company: string | null;
  role: string | null;
  location: string | null;
  seniority: string | null;
  workplace: string | null;
  visa_mentioned: boolean;
  visa_context: string;
  clearance_required: boolean;
  clearance_context: string;
  resolved_by: Record<string, string>;
}

export interface AnalysisResult {
  run_id: string;
  jd_id: number | null;
  master_id: number | null;
  parsed: ParsedJD;
  sections: { kind: string; heading: string; chars: number }[];
  terms: RankedTerm[];
  unknown_terms: { term: string; frequency: number; sections: string[] }[];
  coverage: Coverage;
  similarity_backend: string;
  warnings: string[];
}

export interface MasterResumeRow {
  id: number;
  filename: string;
  format: string;
  tailorable: boolean;
  is_active: boolean;
  reason: string;
}

export const analysisApi = {
  masters: () => request<MasterResumeRow[]>("/api/masters"),
  importMasters: () =>
    request<{ count: number }>("/api/masters/import", { method: "POST" }),
  analyse: (body: { text?: string; url?: string; master_id?: number }) =>
    request<AnalysisResult>("/api/analyse", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
