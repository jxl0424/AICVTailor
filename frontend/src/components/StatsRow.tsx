import { useState } from "react";
import type { ApplicationStats } from "../api";

/**
 * Rates travel with the definitions they were computed from, the same rule the
 * coverage score follows. A bare "38% response rate" invites the wrong
 * conclusion when you cannot see what counted as a response.
 */
export function StatsRow({ stats }: { stats: ApplicationStats }) {
  const [open, setOpen] = useState(false);

  const tiles: { label: string; value: string; key?: string; warn?: boolean }[] = [
    { label: "tracked", value: String(stats.total) },
    { label: "sent", value: String(stats.sent), key: "sent" },
    { label: "active", value: String(stats.active) },
    { label: "stale", value: String(stats.stale), key: "stale", warn: stats.stale > 0 },
    {
      label: "response rate",
      value: `${stats.response_rate}%`,
      key: "response_rate",
    },
    {
      label: "interview rate",
      value: `${stats.interview_rate}%`,
      key: "interview_rate",
    },
    { label: "offers", value: String(stats.offers) },
  ];

  return (
    <div className="rounded border border-ink-700 bg-ink-900 p-3">
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
        {tiles.map((tile) => (
          <div key={tile.label}>
            <div
              className={`text-lg tabular-nums ${tile.warn ? "text-warn" : "text-ink-50"}`}
            >
              {tile.value}
            </div>
            <div className="text-xs text-ink-400">{tile.label}</div>
          </div>
        ))}
        <button
          className="ml-auto text-xs text-accent hover:underline"
          onClick={() => setOpen((v) => !v)}
        >
          {open ? "hide definitions" : "what counts?"}
        </button>
      </div>

      {open && (
        <dl className="mt-3 space-y-1 border-t border-ink-800 pt-2 text-xs">
          {Object.entries(stats.definitions).map(([key, text]) => (
            <div key={key} className="flex gap-2">
              <dt className="w-32 shrink-0 text-ink-400">{key.replace(/_/g, " ")}</dt>
              <dd className="text-ink-200">{text}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
