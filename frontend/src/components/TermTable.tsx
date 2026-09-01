import { useState } from "react";
import type { MatchStatus, RankedTerm } from "../api";

const STATUS_LABEL: Record<MatchStatus, string> = {
  present_exact: "present",
  present_as_synonym: "synonym",
  implied: "implied",
  missing: "missing",
};

const STATUS_CLASS: Record<MatchStatus, string> = {
  present_exact: "text-ok",
  present_as_synonym: "text-ok",
  implied: "text-warn",
  missing: "text-bad",
};

type Filter = "all" | "missing" | "present";

export function TermTable({ terms }: { terms: RankedTerm[] }) {
  const [filter, setFilter] = useState<Filter>("all");
  const [expanded, setExpanded] = useState<string | null>(null);

  const shown = terms.filter((t) => {
    if (filter === "missing") return t.status === "missing" || t.status === "implied";
    if (filter === "present") return t.status.startsWith("present");
    return true;
  });

  return (
    <div>
      <div className="mb-2 flex items-center gap-3 text-xs">
        <span className="text-ink-400">{terms.length} terms, ranked by weight</span>
        <div className="ml-auto flex gap-1">
          {(["all", "missing", "present"] as Filter[]).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`rounded px-2 py-0.5 ${
                filter === f
                  ? "bg-ink-700 text-ink-50"
                  : "text-ink-400 hover:text-ink-200"
              }`}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="tbl">
          <thead>
            <tr>
              <th>term</th>
              <th>category</th>
              <th className="text-right">weight</th>
              <th>section</th>
              <th>status</th>
              <th>where</th>
              <th>evidence</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((t) => (
              <>
                <tr
                  key={t.term}
                  className="cursor-pointer"
                  onClick={() => setExpanded(expanded === t.term ? null : t.term)}
                >
                  <td className="text-ink-50">{t.term}</td>
                  <td className="text-ink-400">{t.category.replace(/_/g, " ")}</td>
                  <td className="text-right tabular-nums">{t.weight.toFixed(2)}</td>
                  <td className="text-ink-400">{t.weight_breakdown.section}</td>
                  <td className={STATUS_CLASS[t.status]}>{STATUS_LABEL[t.status]}</td>
                  <td className="text-ink-400">{t.location ?? "—"}</td>
                  <td className="max-w-md truncate text-ink-400">
                    {t.bullet_id ? (
                      <span className="text-accent">{t.bullet_id}</span>
                    ) : null}{" "}
                    {t.evidence}
                  </td>
                </tr>
                {expanded === t.term && (
                  <tr key={`${t.term}-detail`}>
                    <td colSpan={7} className="bg-ink-900 text-xs text-ink-400">
                      <div className="tabular-nums">{t.weight_formula}</div>
                      <div className="mt-1">
                        appears {t.frequency}× as {t.surfaces.join(", ")} in{" "}
                        {t.sections.join(", ")}
                        {t.status === "implied" &&
                          ` · similarity ${t.match_score} to ${t.bullet_id}`}
                      </div>
                      {t.evidence && <div className="mt-1 italic">{t.evidence}</div>}
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
