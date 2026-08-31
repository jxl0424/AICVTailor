import type { ProbeStatus } from "../api";

const COLOR: Record<ProbeStatus, string> = {
  ok: "bg-ok",
  degraded: "bg-warn",
  unavailable: "bg-bad",
};

export function StatusDot({ status }: { status: ProbeStatus }) {
  return (
    <span
      className={`inline-block h-2 w-2 shrink-0 rounded-full ${COLOR[status]}`}
      title={status}
    />
  );
}
