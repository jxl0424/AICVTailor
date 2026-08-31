import type { ReactNode } from "react";

/** Stub body for a tab that lands in a later phase. Says which phase, so the
 *  app is honest about what is not built yet rather than looking broken. */
export function Placeholder({ phase, children }: { phase: string; children: ReactNode }) {
  return (
    <div className="max-w-2xl px-4 py-8 text-sm">
      <div className="mb-2 inline-block rounded border border-ink-700 px-2 py-0.5 text-xs text-ink-400">
        {phase}
      </div>
      <div className="text-ink-400">{children}</div>
    </div>
  );
}
