import type { ChangedSpan } from "../api";

/**
 * Side-by-side is wasteful for one-line bullets, so changes render inline with
 * word-level highlighting. Every change shows the JD terms it targeted and the
 * source bullet it came from -- that traceability is the point of the view.
 */
export function DiffView({
  changes,
  onReject,
  rejecting,
}: {
  changes: ChangedSpan[];
  onReject?: (targetId: string) => void;
  rejecting?: string | null;
}) {
  if (changes.length === 0) {
    return (
      <p className="text-sm text-ink-400">
        No changes were applied. Accept some suggestions on the Tailor tab first.
      </p>
    );
  }

  const bySection = changes.reduce<Record<string, ChangedSpan[]>>((acc, change) => {
    const key = `${change.section} · ${change.entry}`;
    (acc[key] ||= []).push(change);
    return acc;
  }, {});

  return (
    <div className="space-y-4">
      {Object.entries(bySection).map(([heading, group]) => (
        <section key={heading}>
          <h3 className="mb-1 text-xs uppercase tracking-wide text-ink-400">{heading}</h3>
          <div className="space-y-2">
            {group.map((change) => (
              <div key={change.target_id} className="rounded border border-ink-700 p-2">
                <div className="mb-1 flex flex-wrap items-center gap-2 text-[11px]">
                  <span className="rounded border border-ink-600 px-1.5 py-0.5 text-ink-400">
                    {change.kind}
                  </span>
                  <span className="text-accent">{change.target_id}</span>
                  {change.source_bullet_id && (
                    <span className="text-ink-600">
                      from source bullet{" "}
                      <span className="text-accent">{change.source_bullet_id}</span>
                    </span>
                  )}
                  {change.target_terms.filter(Boolean).length > 0 && (
                    <span className="text-ink-400">
                      targeting {change.target_terms.filter(Boolean).join(", ")}
                    </span>
                  )}
                  {onReject && (
                    <button
                      className="ml-auto rounded border border-ink-700 px-1.5 py-0.5 text-ink-400 hover:border-bad hover:text-bad disabled:opacity-40"
                      onClick={() => onReject(change.target_id)}
                      disabled={rejecting !== null && rejecting !== undefined}
                      title="Drop this change and regenerate, so the compile gate runs again"
                    >
                      {rejecting === change.target_id ? "regenerating…" : "reject"}
                    </button>
                  )}
                </div>

                <p className="font-mono text-[11px] leading-relaxed">
                  {change.pieces.map((piece, i) => (
                    <span
                      key={i}
                      className={
                        piece.kind === "insert"
                          ? "bg-ok/20 text-ok"
                          : piece.kind === "delete"
                            ? "bg-bad/15 text-bad line-through"
                            : "text-ink-300"
                      }
                    >
                      {piece.text}
                    </span>
                  ))}
                </p>
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
