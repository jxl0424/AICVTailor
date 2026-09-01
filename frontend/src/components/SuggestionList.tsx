import { useEffect, useRef, useState } from "react";
import { ApiError, suggestionApi, type SuggestionRow } from "../api";

const ACTION_STYLE: Record<string, string> = {
  REWORD: "border-accent/50 text-accent",
  RELOCATE: "border-ok/50 text-ok",
  GAP: "border-ink-600 text-ink-400",
};

/**
 * Accept/reject with keyboard shortcuts, because this is the screen the user
 * spends the most time on. j/k move, a accepts, r rejects.
 *
 * GAP rows have no accept control at all. The server refuses to accept one,
 * and the UI does not offer a button that would only ever return an error.
 */
export function SuggestionList({
  suggestions,
  onChange,
}: {
  suggestions: SuggestionRow[];
  onChange: (row: SuggestionRow) => void;
}) {
  const [cursor, setCursor] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const rowRefs = useRef<(HTMLDivElement | null)[]>([]);

  async function decide(row: SuggestionRow, accepted: boolean) {
    setError(null);
    try {
      onChange(await suggestionApi.decide(row.id, accepted));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const tag = (event.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      const current = suggestions[cursor];

      if (event.key === "j" || event.key === "ArrowDown") {
        setCursor((c) => Math.min(c + 1, suggestions.length - 1));
      } else if (event.key === "k" || event.key === "ArrowUp") {
        setCursor((c) => Math.max(c - 1, 0));
      } else if (event.key === "a" && current?.applicable) {
        void decide(current, true);
      } else if (event.key === "r" && current) {
        void decide(current, false);
      } else {
        return;
      }
      event.preventDefault();
    }

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cursor, suggestions]);

  useEffect(() => {
    rowRefs.current[cursor]?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  if (suggestions.length === 0) {
    return <p className="text-sm text-ink-400">No suggestions yet.</p>;
  }

  const accepted = suggestions.filter((s) => s.accepted).length;

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3 text-xs text-ink-400">
        <span>
          {suggestions.length} suggestions · {accepted} accepted
        </span>
        <span className="ml-auto text-ink-600">
          <kbd>j</kbd>/<kbd>k</kbd> move · <kbd>a</kbd> accept · <kbd>r</kbd> reject
        </span>
      </div>

      {error && (
        <p className="rounded border border-bad/40 bg-bad/5 p-2 text-xs text-bad">{error}</p>
      )}

      {suggestions.map((s, index) => (
        <div
          key={s.id}
          ref={(el) => {
            rowRefs.current[index] = el;
          }}
          onClick={() => setCursor(index)}
          className={`rounded border p-2 text-xs ${
            index === cursor ? "border-accent/60 bg-ink-900" : "border-ink-700"
          } ${s.accepted ? "bg-ok/5" : ""}`}
        >
          <div className="flex items-center gap-2">
            <span className={`rounded border px-1.5 py-0.5 ${ACTION_STYLE[s.action]}`}>
              {s.action}
            </span>
            <span className="text-ink-50">{s.term}</span>
            <span className="tabular-nums text-ink-600">{s.weight.toFixed(2)}</span>
            {s.source_bullet_id && (
              <span className="text-accent" title="source bullet">
                {s.source_bullet_id}
              </span>
            )}
            <div className="ml-auto flex gap-1">
              {s.applicable ? (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    void decide(s, !s.accepted);
                  }}
                  className={`rounded px-2 py-0.5 ${
                    s.accepted
                      ? "bg-ok/20 text-ok"
                      : "border border-ink-600 text-ink-400 hover:text-ink-200"
                  }`}
                >
                  {s.accepted ? "accepted" : "accept"}
                </button>
              ) : (
                <span className="px-2 py-0.5 text-ink-600" title={s.rationale}>
                  nothing to apply
                </span>
              )}
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  void decide(s, false);
                }}
                className="rounded border border-ink-700 px-2 py-0.5 text-ink-400 hover:text-ink-200"
              >
                reject
              </button>
            </div>
          </div>

          <p className="mt-1 text-ink-400">{s.rationale}</p>

          {s.original_text && s.proposed_text && (
            <div className="mt-2 space-y-1 font-mono text-[11px]">
              <div className="text-ink-600">− {s.original_text}</div>
              <div className="text-ok">+ {s.proposed_text}</div>
            </div>
          )}

          {s.action === "RELOCATE" && s.proposed_text && (
            <div className="mt-2 font-mono text-[11px] text-ok">→ {s.proposed_text}</div>
          )}

          {s.what_it_would_take && (
            <p className="mt-1 italic text-ink-600">{s.what_it_would_take}</p>
          )}

          {s.guardrail_violations && s.guardrail_violations.length > 0 && (
            <ul className="mt-1 list-inside list-disc text-bad">
              {s.guardrail_violations.map((v) => (
                <li key={v.rule + v.offending}>{v.detail}</li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}
