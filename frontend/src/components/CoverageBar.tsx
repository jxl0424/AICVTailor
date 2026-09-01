import { useState } from "react";
import type { Coverage } from "../api";

const STATUS_COLOR: Record<string, string> = {
  present_exact: "bg-ok",
  present_as_synonym: "bg-ok/70",
  implied: "bg-warn",
  missing: "bg-ink-700",
};

/**
 * Shows the number and, on demand, the arithmetic behind it. The brief is
 * explicit that this is not an ATS score, so the disclaimer sits next to the
 * figure rather than in a footnote nobody reads.
 */
export function CoverageBar({ coverage }: { coverage: Coverage }) {
  const [showMaths, setShowMaths] = useState(false);
  const total = Object.values(coverage.counts).reduce((a, b) => a + b, 0);

  return (
    <div className="rounded border border-ink-700 bg-ink-900 p-3">
      <div className="flex items-baseline gap-3">
        <span className="text-2xl tabular-nums text-ink-50">{coverage.percent}%</span>
        <span className="text-xs text-ink-400">JD keyword coverage</span>
        <button
          className="ml-auto text-xs text-accent hover:underline"
          onClick={() => setShowMaths((v) => !v)}
        >
          {showMaths ? "hide maths" : "show maths"}
        </button>
      </div>

      <div className="mt-2 flex h-2 overflow-hidden rounded bg-ink-800">
        {["present_exact", "present_as_synonym", "implied", "missing"].map((status) => {
          const count = coverage.counts[status] ?? 0;
          if (!count) return null;
          return (
            <div
              key={status}
              className={STATUS_COLOR[status]}
              style={{ width: `${(100 * count) / (total || 1)}%` }}
              title={`${status.replace(/_/g, " ")}: ${count}`}
            />
          );
        })}
      </div>

      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-400">
        {coverage.by_category.map((c) => (
          <span key={c.category}>
            {c.category.replace(/_/g, " ")}{" "}
            <span className="tabular-nums text-ink-200">{c.percent.toFixed(0)}%</span>
            <span className="text-ink-600"> ({c.term_count})</span>
          </span>
        ))}
      </div>

      {showMaths && (
        <div className="mt-3 border-t border-ink-800 pt-2 text-xs text-ink-400">
          <div className="tabular-nums">
            covered weight {coverage.covered_weight} ÷ total weight{" "}
            {coverage.total_weight} = {coverage.percent}%
          </div>
          <div className="mt-1">
            credit per term:{" "}
            {Object.entries(coverage.credit_scheme)
              .map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`)
              .join(", ")}
          </div>
          <div className="mt-2 text-ink-400">{coverage.disclaimer}</div>
        </div>
      )}
    </div>
  );
}
