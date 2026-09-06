import { useEffect, useState } from "react";
import { tailorApi, type TailorResult } from "../api";
import { DiffView } from "../components/DiffView";

/**
 * Reads the last tailoring run from sessionStorage so the Tailor tab can hand
 * off without a store. A per-tab convenience, not durable state.
 */
export function Changes() {
  const [result, setResult] = useState<TailorResult | null>(null);

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem("lastTailorRun");
      if (raw) {
        setResult(JSON.parse(raw) as TailorResult);
        return;
      }
    } catch {
      /* storage can be unavailable; fall through to the server */
    }

    // Reloading the page, or opening this tab directly, should still show the
    // most recent tailored version rather than an empty state.
    void tailorApi
      .list()
      .then(async (rows) => {
        if (rows.length === 0) return;
        const detail = await tailorApi.get(rows[0].id);
        setResult({
          run_id: "stored",
          tailored_id: detail.id,
          compiled: detail.compiled,
          compile_error: detail.compile_error ?? "",
          engine: "",
          changes: detail.diff.changes,
          reverted: [],
          verification: {
            ok: true,
            pages: 0,
            max_pages: null,
            page_limit_ok: true,
            grew: false,
            extracted_chars: 0,
            surviving_terms: [],
            lost_terms: [],
            forbidden_hits: [],
            notes: [],
            skipped: true,
          },
          coverage_before: detail.coverage_before ?? 0,
          coverage_after: detail.coverage_after ?? 0,
          document_guardrails: { ok: true, violations: [] },
          warnings: [],
          has_pdf: detail.has_pdf,
          applied: detail.diff.changes.length,
        });
      })
      .catch(() => undefined);
  }, []);

  if (!result) {
    return (
      <div className="max-w-2xl p-4 text-sm text-ink-400">
        Nothing tailored yet. On the Tailor tab, analyse a job description, accept
        some suggestions, then run Tailor.
      </div>
    );
  }

  const v = result.verification;

  return (
    <div className="space-y-4 p-4">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className={result.compiled ? "text-ok" : "text-warn"}>
          {/* A version loaded from the database has no live compile telemetry,
              so it must not report an engine or a page count it never saw. */}
          {result.run_id === "stored"
            ? result.compiled
              ? "stored version · compiled when it was made"
              : `stored version · did not compile: ${result.compile_error}`
            : result.compiled
              ? `compiled with ${result.engine} · ${v.pages} page${v.pages === 1 ? "" : "s"}`
              : v.skipped
                ? "no LaTeX engine: .tex only"
                : `compile failed: ${result.compile_error}`}
        </span>
        <span className="text-ink-400">
          coverage {result.coverage_before}% → {result.coverage_after}%
        </span>
        <span className="text-ink-400">{result.applied} change(s)</span>

        <div className="ml-auto flex gap-2">
          <a
            href={tailorApi.texUrl(result.tailored_id)}
            className="rounded border border-ink-700 px-2 py-1 text-xs text-ink-200 hover:border-accent"
          >
            download .tex
          </a>
          <a
            href={tailorApi.pdfUrl(result.tailored_id)}
            className={`rounded border px-2 py-1 text-xs ${
              result.has_pdf
                ? "border-ink-700 text-ink-200 hover:border-accent"
                : "pointer-events-none border-ink-800 text-ink-600"
            }`}
            title={result.has_pdf ? "" : "No PDF was produced"}
          >
            download .pdf
          </a>
        </div>
      </div>

      {v.lost_terms.length > 0 && (
        <p className="rounded border border-warn/40 bg-warn/5 p-2 text-xs text-warn">
          In the .tex but not in the extracted PDF text:{" "}
          <strong>{v.lost_terms.join(", ")}</strong>. A parser reads the extracted
          text, so these will not count.
        </p>
      )}

      {result.reverted.length > 0 && (
        <div className="rounded border border-bad/40 bg-bad/5 p-2 text-xs text-bad">
          <p className="mb-1">
            {result.reverted.length} edit(s) were reverted because they broke the
            build. The original bullets were kept so the file still compiles.
          </p>
          <ul className="list-inside list-disc">
            {result.reverted.map((r) => (
              <li key={r.target_id}>
                <span className="font-mono">{r.target_id}</span>: {r.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      {result.warnings
        .filter((w) => !w.startsWith("reverted the edit"))
        .map((w) => (
          <p key={w} className="rounded border border-warn/30 bg-warn/5 p-2 text-xs text-warn">
            {w}
          </p>
        ))}

      <DiffView changes={result.changes} />

      {!v.skipped && (
        <p className="text-xs text-ink-600">
          run {result.run_id} · verified {v.surviving_terms.length} term(s) survived
          into {v.extracted_chars} characters of extracted PDF text
        </p>
      )}
    </div>
  );
}
