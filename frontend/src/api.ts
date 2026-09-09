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
  terms_scored: number;
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

export interface JDRow {
  id: number;
  company: string | null;
  role: string | null;
  location: string | null;
  ingested_at: string;
  term_count: number;
}

export const analysisApi = {
  masters: () => request<MasterResumeRow[]>("/api/masters"),
  jds: () => request<JDRow[]>("/api/jds"),
  importMasters: () =>
    request<{ count: number }>("/api/masters/import", { method: "POST" }),
  analyse: (body: { text?: string; url?: string; master_id?: number }) =>
    request<AnalysisResult>("/api/analyse", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};

// --- suggestions ------------------------------------------------------------

export type SuggestionAction = "REWORD" | "RELOCATE" | "GAP";

export interface GuardrailViolation {
  rule: string;
  detail: string;
  offending: string;
}

export interface SuggestionRow {
  id: number;
  term: string;
  category: string;
  weight: number;
  status: string;
  action: SuggestionAction;
  proposed_text: string | null;
  original_text?: string;
  source_bullet_id: string | null;
  target_id: string | null;
  rationale: string;
  guardrail_violations: GuardrailViolation[] | null;
  accepted: boolean;
  applicable: boolean;
  what_it_would_take?: string;
}

export const suggestionApi = {
  generate: (jd_id: number) =>
    request<{
      run_id: string;
      provider_available: boolean;
      provider_error: string;
      suggestions: SuggestionRow[];
    }>("/api/suggest", { method: "POST", body: JSON.stringify({ jd_id }) }),
  list: (jd_id: number) => request<SuggestionRow[]>(`/api/jds/${jd_id}/suggestions`),
  decide: (id: number, accepted: boolean) =>
    request<SuggestionRow>(`/api/suggestions/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ accepted }),
    }),
};

// --- tailoring --------------------------------------------------------------

export interface DiffPiece {
  kind: "equal" | "insert" | "delete";
  text: string;
}

export interface ChangedSpan {
  target_id: string;
  kind: "bullet" | "skills";
  section: string;
  entry: string;
  source_bullet_id: string | null;
  target_terms: string[];
  before: string;
  after: string;
  pieces: DiffPiece[];
}

export interface VerificationReport {
  ok: boolean;
  pages: number;
  max_pages: number | null;
  page_limit_ok: boolean;
  grew: boolean;
  extracted_chars: number;
  surviving_terms: string[];
  lost_terms: string[];
  forbidden_hits: string[];
  notes: string[];
  skipped: boolean;
}

export interface TailorResult {
  run_id: string;
  tailored_id: number;
  compiled: boolean;
  compile_error: string;
  engine: string;
  changes: ChangedSpan[];
  reverted: { target_id: string; reason: string }[];
  verification: VerificationReport;
  coverage_before: number;
  coverage_after: number;
  document_guardrails: { ok: boolean; violations: { detail: string }[] };
  warnings: string[];
  has_pdf: boolean;
  applied: number;
}

export interface TailoredRow {
  id: number;
  jd_id: number | null;
  company: string | null;
  role: string | null;
  created_at: string;
  compiled: boolean;
  coverage_before: number | null;
  coverage_after: number | null;
  changes: number;
  has_pdf: boolean;
  orphaned: boolean;
  coverage_delta: number | null;
}

export interface LibraryFilters {
  company?: string;
  role?: string;
  since?: string;
  until?: string;
  compiled_only?: boolean;
}

export const tailorApi = {
  list: (filters: LibraryFilters = {}) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== "" && value !== false) {
        params.set(key, String(value));
      }
    }
    const query = params.toString();
    return request<TailoredRow[]>(`/api/tailored${query ? `?${query}` : ""}`);
  },
  duplicate: (id: number, jd_id: number) =>
    request<{ copied: string[]; stale: string[]; note: string }>(
      `/api/tailored/${id}/duplicate`,
      { method: "POST", body: JSON.stringify({ jd_id }) },
    ),
  rejectChange: (id: number, target_id: string) =>
    request<TailorResult>(
      `/api/tailored/${id}/reject-change?target_id=${encodeURIComponent(target_id)}`,
      { method: "POST" },
    ),
  run: (jd_id: number) =>
    request<TailorResult>("/api/tailor", {
      method: "POST",
      body: JSON.stringify({ jd_id }),
    }),
  get: (id: number) =>
    request<{
      id: number;
      company: string | null;
      role: string | null;
      compiled: boolean;
      compile_error: string | null;
      coverage_before: number | null;
      coverage_after: number | null;
      diff: { changes: ChangedSpan[] };
      has_pdf: boolean;
    }>(`/api/tailored/${id}`),
  texUrl: (id: number) => `/api/tailored/${id}/download.tex`,
  pdfUrl: (id: number) => `/api/tailored/${id}/download.pdf`,
};

// --- applications -----------------------------------------------------------

export const APPLICATION_STATUSES = [
  "saved",
  "applied",
  "screening",
  "interview_1",
  "interview_2",
  "take_home",
  "offer",
  "rejected",
  "ghosted",
  "withdrawn",
] as const;

export type ApplicationStatus = (typeof APPLICATION_STATUSES)[number];

export interface ApplicationRow {
  id: number;
  company: string;
  role: string;
  jd_id: number | null;
  tailored_resume_id: number | null;
  status: ApplicationStatus;
  applied_on: string | null;
  source: string | null;
  salary_range: string | null;
  contact_name: string | null;
  next_action: string | null;
  next_action_date: string | null;
  notes: string | null;
  created_at: string;
  last_movement_at: string;
  days_since_movement: number;
  stale: boolean;
}

export interface CompanyHistory {
  company: string;
  applications: number;
  statuses: string[];
  last_applied: string | null;
  responded: number;
}

export interface ApplicationStats {
  total: number;
  sent: number;
  responded: number;
  interviewed: number;
  offers: number;
  active: number;
  stale: number;
  response_rate: number;
  interview_rate: number;
  offer_rate: number;
  by_status: Record<string, number>;
  per_company: CompanyHistory[];
  definitions: Record<string, string>;
}

export const applicationApi = {
  list: (filters: { status?: string; company?: string; stale_only?: boolean } = {}) => {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(filters)) {
      if (v !== undefined && v !== "" && v !== false) params.set(k, String(v));
    }
    const q = params.toString();
    return request<ApplicationRow[]>(`/api/applications${q ? `?${q}` : ""}`);
  },
  create: (body: { company: string; role: string; status?: string }) =>
    request<ApplicationRow>("/api/applications", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  fromTailored: (tailored_id: number) =>
    request<ApplicationRow>("/api/applications/from-tailored", {
      method: "POST",
      body: JSON.stringify({ tailored_id }),
    }),
  update: (id: number, patch: Partial<ApplicationRow>) =>
    request<ApplicationRow>(`/api/applications/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  remove: (id: number) =>
    request<{ deleted: number }>(`/api/applications/${id}`, { method: "DELETE" }),
  stats: () => request<ApplicationStats>("/api/applications/stats"),
  csvUrl: () => "/api/applications/export.csv",
};
