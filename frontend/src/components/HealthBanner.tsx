import { useState } from "react";
import { api } from "../api";
import { useAsync } from "../useAsync";
import { StatusDot } from "./StatusDot";

const HEADLINE: Record<string, string> = {
  ok: "All components ready.",
  degraded: "Running with reduced capability.",
  unavailable: "Not ready — no usable LLM provider.",
};

/**
 * Renders each probe's own `detail` and `fallback` verbatim. The backend owns
 * that copy so there is exactly one place that explains what is missing and
 * what happens instead.
 */
export function HealthBanner() {
  const { data, error, loading, reload } = useAsync(() => api.health());
  const [open, setOpen] = useState(false);

  if (loading && !data) {
    return <div className="px-4 py-2 text-xs text-ink-400">Checking components…</div>;
  }
  if (error) {
    return (
      <div className="border-b border-ink-700 bg-ink-900 px-4 py-2 text-xs text-bad">
        Backend unreachable: {error.message}
      </div>
    );
  }
  if (!data) return null;

  const problems = data.probes.filter((p) => p.status !== "ok");

  return (
    <div className="border-b border-ink-700 bg-ink-900 text-xs">
      <div className="flex items-center gap-2 px-4 py-2">
        <StatusDot status={data.status} />
        <span className="text-ink-200">{HEADLINE[data.status]}</span>
        <span className="text-ink-400">
          provider: <span className="text-ink-200">{data.provider}</span>
        </span>
        {problems.length > 0 && (
          <button
            className="text-accent hover:underline"
            onClick={() => setOpen((v) => !v)}
          >
            {open ? "hide" : `${problems.length} issue${problems.length > 1 ? "s" : ""}`}
          </button>
        )}
        <button
          className="ml-auto text-ink-400 hover:text-ink-200"
          onClick={() => {
            api.reloadConfig().then(reload).catch(reload);
          }}
          title="Re-read .env and config/*.yaml, then re-probe"
        >
          reload config
        </button>
      </div>

      {open && (
        <table className="tbl border-t border-ink-800">
          <tbody>
            {data.probes.map((p) => (
              <tr key={p.name}>
                <td className="w-40">
                  <span className="flex items-center gap-2">
                    <StatusDot status={p.status} />
                    <span className="text-ink-200">{p.name}</span>
                  </span>
                </td>
                <td>
                  <div className="text-ink-200">{p.detail}</div>
                  {p.fallback && <div className="mt-0.5 text-ink-400">→ {p.fallback}</div>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
