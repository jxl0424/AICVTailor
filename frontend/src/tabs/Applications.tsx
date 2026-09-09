import { useCallback, useEffect, useState } from "react";
import {
  APPLICATION_STATUSES,
  ApiError,
  applicationApi,
  type ApplicationRow,
  type ApplicationStats,
  type ApplicationStatus,
} from "../api";
import { StatsRow } from "../components/StatsRow";

const KANBAN_COLUMNS: ApplicationStatus[] = [
  "saved",
  "applied",
  "screening",
  "interview_1",
  "interview_2",
  "take_home",
  "offer",
];

function label(status: string) {
  return status.replace(/_/g, " ");
}

export function Applications() {
  const [rows, setRows] = useState<ApplicationRow[]>([]);
  const [stats, setStats] = useState<ApplicationStats | null>(null);
  const [view, setView] = useState<"table" | "kanban">("table");
  const [staleOnly, setStaleOnly] = useState(false);
  const [company, setCompany] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ id: number; field: string } | null>(null);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({ company: "", role: "" });

  const load = useCallback(() => {
    Promise.all([
      applicationApi.list({ company: company || undefined, stale_only: staleOnly }),
      applicationApi.stats(),
    ])
      .then(([list, s]) => {
        setRows(list);
        setStats(s);
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : String(err)));
  }, [company, staleOnly]);

  useEffect(() => load(), [load]);

  async function patch(row: ApplicationRow, field: string, value: string) {
    setEditing(null);
    try {
      await applicationApi.update(row.id, { [field]: value || null } as never);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function add() {
    if (!draft.company.trim() || !draft.role.trim()) return;
    try {
      await applicationApi.create(draft);
      setDraft({ company: "", role: "" });
      setAdding(false);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  /** Click a cell to edit it in place; Enter or blur commits, Escape cancels. */
  function EditableCell({
    row,
    field,
    value,
    type = "text",
    placeholder = "—",
  }: {
    row: ApplicationRow;
    field: string;
    value: string | null;
    type?: string;
    placeholder?: string;
  }) {
    const active = editing?.id === row.id && editing.field === field;
    if (!active) {
      return (
        <span
          className="cursor-text text-ink-200 hover:text-ink-50"
          onClick={() => setEditing({ id: row.id, field })}
        >
          {value || <span className="text-ink-600">{placeholder}</span>}
        </span>
      );
    }
    return (
      <input
        autoFocus
        type={type}
        defaultValue={value ?? ""}
        className="w-full rounded border border-accent bg-ink-950 px-1 text-ink-50"
        onBlur={(e) => patch(row, field, e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          if (e.key === "Escape") setEditing(null);
        }}
      />
    );
  }

  return (
    <div className="space-y-3 p-4">
      {stats && <StatsRow stats={stats} />}

      <div className="flex flex-wrap items-center gap-2 text-xs">
        {(["table", "kanban"] as const).map((v) => (
          <button
            key={v}
            onClick={() => setView(v)}
            className={`rounded px-2 py-0.5 ${
              view === v ? "bg-ink-700 text-ink-50" : "text-ink-400 hover:text-ink-200"
            }`}
          >
            {v}
          </button>
        ))}
        <input
          className="w-40 rounded border border-ink-700 bg-ink-900 px-2 py-1 text-ink-200 placeholder:text-ink-600"
          placeholder="company"
          value={company}
          onChange={(e) => setCompany(e.target.value)}
        />
        <label className="flex items-center gap-1 text-ink-400">
          <input
            type="checkbox"
            checked={staleOnly}
            onChange={(e) => setStaleOnly(e.target.checked)}
          />
          stale only
        </label>

        <div className="ml-auto flex gap-2">
          <button
            className="rounded border border-ink-700 px-2 py-0.5 text-ink-400 hover:text-ink-200"
            onClick={() => setAdding((v) => !v)}
          >
            + add
          </button>
          <a
            className="rounded border border-ink-700 px-2 py-0.5 text-ink-200 hover:border-accent"
            href={applicationApi.csvUrl()}
          >
            export CSV
          </a>
        </div>
      </div>

      {adding && (
        <div className="flex items-center gap-2 rounded border border-ink-700 p-2 text-xs">
          <input
            autoFocus
            className="rounded border border-ink-700 bg-ink-900 px-2 py-1 text-ink-200"
            placeholder="company"
            value={draft.company}
            onChange={(e) => setDraft((d) => ({ ...d, company: e.target.value }))}
          />
          <input
            className="rounded border border-ink-700 bg-ink-900 px-2 py-1 text-ink-200"
            placeholder="role"
            value={draft.role}
            onChange={(e) => setDraft((d) => ({ ...d, role: e.target.value }))}
            onKeyDown={(e) => e.key === "Enter" && add()}
          />
          <button className="text-accent hover:underline" onClick={add}>
            save
          </button>
        </div>
      )}

      {error && (
        <p className="rounded border border-bad/40 bg-bad/5 p-2 text-xs text-bad">{error}</p>
      )}

      {rows.length === 0 ? (
        <p className="text-sm text-ink-400">
          No applications yet. Add one above, or start tracking from a tailored
          version in the Library.
        </p>
      ) : view === "table" ? (
        <div className="overflow-x-auto">
          <table className="tbl">
            <thead>
              <tr>
                <th>company</th>
                <th>role</th>
                <th>status</th>
                <th>applied</th>
                <th className="text-right" title="days since the status last changed">
                  idle
                </th>
                <th>next action</th>
                <th>due</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.id}
                  /* Stale rows get a left rule rather than a wash, so the row
                     stays readable while still catching the eye. */
                  className={row.stale ? "border-l-2 border-l-warn bg-warn/5" : ""}
                >
                  <td className="text-ink-50">
                    <EditableCell row={row} field="company" value={row.company} />
                  </td>
                  <td>
                    <EditableCell row={row} field="role" value={row.role} />
                  </td>
                  <td>
                    <select
                      value={row.status}
                      onChange={(e) => patch(row, "status", e.target.value)}
                      className="rounded border border-ink-700 bg-ink-900 px-1 py-0.5 text-ink-200"
                    >
                      {APPLICATION_STATUSES.map((s) => (
                        <option key={s} value={s}>
                          {label(s)}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="whitespace-nowrap text-ink-400">
                    <EditableCell
                      row={row}
                      field="applied_on"
                      value={row.applied_on}
                      type="date"
                    />
                  </td>
                  <td
                    className={`text-right tabular-nums ${
                      row.stale ? "text-warn" : "text-ink-400"
                    }`}
                    title={row.stale ? "no movement for over 14 days" : ""}
                  >
                    {row.days_since_movement}d
                  </td>
                  <td>
                    <EditableCell row={row} field="next_action" value={row.next_action} />
                  </td>
                  <td className="whitespace-nowrap text-ink-400">
                    <EditableCell
                      row={row}
                      field="next_action_date"
                      value={row.next_action_date}
                      type="date"
                    />
                  </td>
                  <td>
                    <button
                      className="text-ink-600 hover:text-bad"
                      onClick={() => applicationApi.remove(row.id).then(load)}
                      title="delete"
                    >
                      ×
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="flex gap-2 overflow-x-auto">
          {KANBAN_COLUMNS.map((status) => {
            const column = rows.filter((r) => r.status === status);
            return (
              <div key={status} className="w-52 shrink-0">
                <div className="mb-1 flex items-baseline justify-between text-xs">
                  <span className="text-ink-400">{label(status)}</span>
                  <span className="tabular-nums text-ink-600">{column.length}</span>
                </div>
                <div className="space-y-1">
                  {column.map((row) => (
                    <div
                      key={row.id}
                      className={`rounded border p-2 text-xs ${
                        row.stale ? "border-warn/50 bg-warn/5" : "border-ink-700"
                      }`}
                    >
                      <div className="text-ink-50">{row.company}</div>
                      <div className="text-ink-400">{row.role}</div>
                      <div className="mt-1 flex items-center gap-2 text-ink-600">
                        <span>{row.days_since_movement}d idle</span>
                        {row.next_action && <span title={row.next_action}>· next action</span>}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {stats && stats.per_company.some((c) => c.applications > 1) && (
        <details className="rounded border border-ink-700 p-2 text-xs">
          <summary className="cursor-pointer text-ink-400">
            companies you have applied to more than once
          </summary>
          <table className="tbl mt-2">
            <tbody>
              {stats.per_company
                .filter((c) => c.applications > 1)
                .map((c) => (
                  <tr key={c.company}>
                    <td className="text-ink-50">{c.company}</td>
                    <td className="tabular-nums">{c.applications} applications</td>
                    <td className="text-ink-400">{c.statuses.map(label).join(", ")}</td>
                    <td className="text-ink-400">
                      {c.responded} responded
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
}
