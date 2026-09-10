import { useEffect, useState } from "react";
import {
  ApiError,
  analysisApi,
  suggestionApi,
  tailorApi,
  type AnalysisResult,
  type MasterResumeRow,
  type SuggestionRow,
} from "../api";
import { useTailorSession } from "../TailorSession";
import { CoverageBar } from "../components/CoverageBar";
import { SuggestionList } from "../components/SuggestionList";
import { TermTable } from "../components/TermTable";

export function Tailor() {
  // Work in progress lives above the router; switching tabs must not lose it.
  const { session, update } = useTailorSession();
  const { jdText, jdUrl, masterId, result, suggestions, view, tailorNote, providerNote } =
    session;

  const setJdText = (v: string) => update({ jdText: v });
  const setJdUrl = (v: string) => update({ jdUrl: v });
  const setMasterId = (v: number | undefined) => update({ masterId: v });
  const setResult = (v: AnalysisResult | null) => update({ result: v });
  const setSuggestions = (v: SuggestionRow[]) => update({ suggestions: v });
  const setView = (v: "terms" | "suggestions") => update({ view: v });
  const setTailorNote = (v: string) => update({ tailorNote: v });
  const setProviderNote = (v: string) => update({ providerNote: v });

  // Transient: restoring these from a previous visit would be misleading.
  const [masters, setMasters] = useState<MasterResumeRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [suggesting, setSuggesting] = useState(false);
  const [tailoring, setTailoring] = useState(false);

  const loadMasters = () =>
    analysisApi
      .masters()
      .then((rows) => {
        setMasters(rows);
        if (masterId === undefined) {
          setMasterId(rows.find((r) => r.is_active)?.id ?? rows[0]?.id);
        }
      })
      .catch(() => undefined);

  useEffect(() => {
    void loadMasters();
  }, []);

  const active = masters.find((m) => m.id === masterId);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const analysis = await analysisApi.analyse({
        text: jdText.trim() || undefined,
        url: jdUrl.trim() || undefined,
        master_id: masterId,
      });
      setResult(analysis);
      setSuggestions([]);
      setProviderNote("");
      setView("terms");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function suggest() {
    if (!result?.jd_id) return;
    setSuggesting(true);
    setError(null);
    try {
      const created = await suggestionApi.generate(result.jd_id);
      setSuggestions(created.suggestions);
      setView("suggestions");
      setProviderNote(
        created.provider_available
          ? ""
          : `No LLM provider available (${created.provider_error}). Rewrites need one; ` +
            "gaps and relocations do not.",
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSuggesting(false);
    }
  }

  async function runTailor() {
    if (!result?.jd_id) return;
    setTailoring(true);
    setError(null);
    try {
      const run = await tailorApi.run(result.jd_id);
      try {
        sessionStorage.setItem("lastTailorRun", JSON.stringify(run));
      } catch {
        /* storage unavailable: the Changes tab will show its empty state */
      }
      setTailorNote(
        `${run.applied} change(s) applied. ` +
          (run.compiled ? `Compiled, ${run.verification.pages} page(s).` : "Not compiled.") +
          " See the Changes tab.",
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setTailoring(false);
    }
  }

  const acceptedCount = suggestions.filter((s) => s.accepted).length;

  return (
    <div className="grid grid-cols-1 gap-4 p-4 lg:grid-cols-[380px_1fr]">
      <section className="space-y-3">
        <div className="flex items-center gap-2 text-xs">
          <label className="text-ink-400">master</label>
          <select
            className="flex-1 rounded border border-ink-700 bg-ink-900 px-2 py-1 text-ink-200"
            value={masterId ?? ""}
            onChange={(e) => setMasterId(Number(e.target.value))}
          >
            {masters.length === 0 && <option value="">none imported</option>}
            {masters.map((m) => (
              <option key={m.id} value={m.id}>
                {m.filename} {m.tailorable ? "" : "(analysis only)"}
              </option>
            ))}
          </select>
          <button
            className="rounded border border-ink-700 px-2 py-1 text-ink-400 hover:text-ink-200"
            onClick={() => analysisApi.importMasters().then(loadMasters)}
          >
            rescan
          </button>
        </div>

        {active && !active.tailorable && (
          <p className="rounded border border-warn/40 bg-warn/5 p-2 text-xs text-warn">
            {active.reason}
          </p>
        )}

        <textarea
          className="h-72 w-full resize-y rounded border border-ink-700 bg-ink-900 p-2 font-mono text-xs text-ink-200 placeholder:text-ink-600"
          placeholder="Paste the job description here."
          value={jdText}
          onChange={(e) => setJdText(e.target.value)}
        />

        <div className="flex items-center gap-2 text-xs">
          <span className="text-ink-600">or</span>
          <input
            className="flex-1 rounded border border-ink-700 bg-ink-900 px-2 py-1 text-ink-200 placeholder:text-ink-600"
            placeholder="https://… (many boards block this; pasting is reliable)"
            value={jdUrl}
            onChange={(e) => setJdUrl(e.target.value)}
          />
        </div>

        <button
          className="w-full rounded bg-accent/90 py-1.5 text-sm text-ink-950 hover:bg-accent disabled:opacity-40"
          onClick={run}
          disabled={busy || (!jdText.trim() && !jdUrl.trim()) || !masterId}
        >
          {busy ? "analysing…" : "Analyse"}
        </button>

        {error && (
          <p className="rounded border border-bad/40 bg-bad/5 p-2 text-xs text-bad">{error}</p>
        )}
      </section>

      <section className="space-y-4">
        {!result && (
          <p className="text-sm text-ink-400">
            Paste a job description to see which of its terms your resume already
            covers. This step only measures — nothing is rewritten.
          </p>
        )}

        {result && (
          <>
            <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-sm">
              <span className="text-ink-50">{result.parsed.role ?? "role unknown"}</span>
              <span className="text-ink-400">{result.parsed.company ?? ""}</span>
              <span className="text-xs text-ink-400">
                {[result.parsed.location, result.parsed.workplace, result.parsed.seniority]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </div>

            {(result.parsed.visa_mentioned || result.parsed.clearance_required) && (
              <div className="rounded border border-warn/40 bg-warn/5 p-2 text-xs text-warn">
                {result.parsed.visa_mentioned && (
                  <div>Visa/right-to-work: “{result.parsed.visa_context}”</div>
                )}
                {result.parsed.clearance_required && (
                  <div>Clearance: “{result.parsed.clearance_context}”</div>
                )}
              </div>
            )}

            <CoverageBar coverage={result.coverage} />

            {result.warnings.map((w) => (
              <p key={w} className="rounded border border-warn/30 bg-warn/5 p-2 text-xs text-warn">
                {w}
              </p>
            ))}

            <div className="flex items-center gap-2 border-b border-ink-800 text-xs">
              {(["terms", "suggestions"] as const).map((v) => (
                <button
                  key={v}
                  onClick={() => setView(v)}
                  className={`border-b-2 px-2 py-1.5 ${
                    view === v
                      ? "border-accent text-ink-50"
                      : "border-transparent text-ink-400 hover:text-ink-200"
                  }`}
                >
                  {v === "terms" ? `terms (${result.terms.length})` : `suggestions (${suggestions.length})`}
                </button>
              ))}
              <button
                className="ml-auto rounded border border-ink-700 px-2 py-0.5 text-ink-400 hover:text-ink-200 disabled:opacity-40"
                onClick={suggest}
                disabled={suggesting}
              >
                {suggesting ? "generating…" : "Generate suggestions"}
              </button>
              <button
                className="rounded bg-accent/90 px-2 py-0.5 text-ink-950 hover:bg-accent disabled:opacity-30"
                onClick={runTailor}
                disabled={tailoring || acceptedCount === 0}
                title={
                  acceptedCount === 0
                    ? "Accept at least one suggestion first"
                    : `Apply ${acceptedCount} accepted change(s)`
                }
              >
                {tailoring
                  ? "tailoring…"
                  : acceptedCount === 0
                    ? "Tailor"
                    : `Tailor (${acceptedCount})`}
              </button>
            </div>

            {suggestions.length === 0 && (
              <p className="rounded border border-accent/30 bg-accent/5 p-2 text-xs text-ink-200">
                <strong>Next:</strong> click <em>Generate suggestions</em>. Nothing is
                rewritten until you accept a suggestion and press Tailor.
              </p>
            )}

            {suggestions.length > 0 && acceptedCount === 0 && (
              <p className="rounded border border-accent/30 bg-accent/5 p-2 text-xs text-ink-200">
                <strong>Next:</strong> accept the suggestions you want on the
                suggestions tab, then press Tailor. GAP rows cannot be accepted -
                nothing in your CV supports them.
              </p>
            )}

            {tailorNote && (
              <p className="rounded border border-ok/40 bg-ok/5 p-2 text-xs text-ok">
                {tailorNote}
              </p>
            )}

            {providerNote && (
              <p className="rounded border border-warn/30 bg-warn/5 p-2 text-xs text-warn">
                {providerNote}
              </p>
            )}

            {view === "terms" ? (
              <TermTable terms={result.terms} />
            ) : (
              <SuggestionList
                suggestions={suggestions}
                onChange={(row) =>
                  setSuggestions(
                    suggestions.map((r) => (r.id === row.id ? { ...r, ...row } : r)),
                  )
                }
              />
            )}

            {result.unknown_terms.length > 0 && (
              <details className="rounded border border-ink-700 p-2 text-xs">
                <summary className="cursor-pointer text-ink-400">
                  {result.unknown_terms.length} repeated phrases not in your skills
                  dictionary
                </summary>
                <p className="mt-2 text-ink-600">
                  Not scored. Add the ones that are real skills to config/skills.yaml
                  so future postings match them.
                </p>
                <div className="mt-2 flex flex-wrap gap-1">
                  {result.unknown_terms.map((u) => (
                    <span key={u.term} className="rounded bg-ink-800 px-1.5 py-0.5 text-ink-300">
                      {u.term} <span className="text-ink-600">×{u.frequency}</span>
                    </span>
                  ))}
                </div>
              </details>
            )}

            <p className="text-xs text-ink-600">
              run {result.run_id} · matching backend: {result.similarity_backend}
            </p>
          </>
        )}
      </section>
    </div>
  );
}
