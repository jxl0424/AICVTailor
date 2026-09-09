import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ApiError,
  analysisApi,
  tailorApi,
  type JDRow,
  type LibraryFilters,
  type TailoredRow,
} from "../api";

/**
 * Every tailored version, filterable, each row linking to its diff, its JD and
 * its downloads. Dense table rather than cards: this is a list to scan, not a
 * gallery to browse.
 */
export function Library() {
  const navigate = useNavigate();
  const [rows, setRows] = useState<TailoredRow[]>([]);
  const [jds, setJds] = useState<JDRow[]>([]);
  const [filters, setFilters] = useState<LibraryFilters>({});
  const [note, setNote] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [duplicating, setDuplicating] = useState<number | null>(null);

  const load = useCallback(() => {
    tailorApi
      .list(filters)
      .then(setRows)
      .catch((err) => setError(err instanceof ApiError ? err.message : String(err)));
  }, [filters]);

  useEffect(() => load(), [load]);
  useEffect(() => {
    void analysisApi.jds().then(setJds).catch(() => undefined);
  }, []);

  async function duplicate(row: TailoredRow, jdId: number) {
    setDuplicating(null);
    setError(null);
    try {
      const result = await tailorApi.duplicate(row.id, jdId);
      setNote(
        `Carried ${result.copied.length} edit(s) onto that job description as ` +
          `pre-accepted suggestions. ${result.note}`.trim(),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  const set = (key: keyof LibraryFilters) => (event: { target: { value: string } }) =>
    setFilters((f) => ({ ...f, [key]: event.target.value || undefined }));

  return (
    <div className="space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <input
          className="w-40 rounded border border-ink-700 bg-ink-900 px-2 py-1 text-ink-200 placeholder:text-ink-600"
          placeholder="company"
          onChange={set("company")}
        />
        <input
          className="w-40 rounded border border-ink-700 bg-ink-900 px-2 py-1 text-ink-200 placeholder:text-ink-600"
          placeholder="role"
          onChange={set("role")}
        />
        <label className="flex items-center gap-1 text-ink-400">
          from
          <input
            type="date"
            className="rounded border border-ink-700 bg-ink-900 px-2 py-1 text-ink-200"
            onChange={set("since")}
          />
        </label>
        <label className="flex items-center gap-1 text-ink-400">
          to
          <input
            type="date"
            className="rounded border border-ink-700 bg-ink-900 px-2 py-1 text-ink-200"
            onChange={set("until")}
          />
        </label>
        <label className="flex items-center gap-1 text-ink-400">
          <input
            type="checkbox"
            onChange={(e) =>
              setFilters((f) => ({ ...f, compiled_only: e.target.checked || undefined }))
            }
          />
          compiled only
        </label>
        <span className="ml-auto text-ink-600">{rows.length} version(s)</span>
      </div>

      {note && (
        <p className="rounded border border-ok/40 bg-ok/5 p-2 text-xs text-ok">{note}</p>
      )}
      {error && (
        <p className="rounded border border-bad/40 bg-bad/5 p-2 text-xs text-bad">{error}</p>
      )}

      {rows.length === 0 ? (
        <p className="text-sm text-ink-400">
          No tailored versions match. Run a tailoring pass on the Tailor tab.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="tbl">
            <thead>
              <tr>
                <th>company</th>
                <th>role</th>
                <th>created</th>
                <th className="text-right">changes</th>
                <th className="text-right">coverage</th>
                <th>state</th>
                <th>links</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td className="text-ink-50">{row.company ?? "—"}</td>
                  <td>{row.role ?? "—"}</td>
                  <td className="whitespace-nowrap text-ink-400">
                    {new Date(row.created_at).toLocaleDateString()}
                  </td>
                  <td className="text-right tabular-nums">{row.changes}</td>
                  <td className="text-right tabular-nums">
                    {row.coverage_before !== null && row.coverage_after !== null ? (
                      <>
                        <span className="text-ink-400">{row.coverage_before}%</span>
                        <span className="text-ink-600"> → </span>
                        <span
                          className={
                            (row.coverage_delta ?? 0) > 0 ? "text-ok" : "text-ink-200"
                          }
                        >
                          {row.coverage_after}%
                        </span>
                      </>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>
                    <span className={row.compiled ? "text-ok" : "text-warn"}>
                      {row.compiled ? "compiled" : "tex only"}
                    </span>
                    {row.orphaned && (
                      <span className="ml-1 text-ink-600" title="its job description was deleted">
                        · orphan
                      </span>
                    )}
                  </td>
                  <td className="whitespace-nowrap">
                    <button
                      className="text-accent hover:underline"
                      onClick={() => navigate(`/changes/${row.id}`)}
                    >
                      diff
                    </button>
                    <span className="text-ink-700"> · </span>
                    <a className="text-accent hover:underline" href={tailorApi.texUrl(row.id)}>
                      tex
                    </a>
                    {row.has_pdf && (
                      <>
                        <span className="text-ink-700"> · </span>
                        <a className="text-accent hover:underline" href={tailorApi.pdfUrl(row.id)}>
                          pdf
                        </a>
                      </>
                    )}
                    <span className="text-ink-700"> · </span>
                    {duplicating === row.id ? (
                      <select
                        autoFocus
                        className="rounded border border-ink-700 bg-ink-900 px-1 text-ink-200"
                        defaultValue=""
                        onChange={(e) => e.target.value && duplicate(row, Number(e.target.value))}
                        onBlur={() => setDuplicating(null)}
                      >
                        <option value="" disabled>
                          apply to which JD?
                        </option>
                        {jds.map((jd) => (
                          <option key={jd.id} value={jd.id}>
                            {jd.company ?? "?"} — {jd.role ?? "?"}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <button
                        className="text-accent hover:underline"
                        onClick={() => setDuplicating(row.id)}
                        title="Carry these edits onto another job description"
                      >
                        reuse
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
