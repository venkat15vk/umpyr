import { useState } from "react";
import { metricsFor } from "../lib/metrics";

/** Four impact tiles with a "?" that opens the metric's definition. Values are illustrative targets, labelled as such. */
export function MetricsStrip({ workspace }: { workspace: string }) {
  const [open, setOpen] = useState<string | null>(null);
  const metrics = metricsFor(workspace);
  return (
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
      {metrics.map((m) => (
        <div key={m.key} className="panel relative px-3 py-2">
          <div className="flex items-start justify-between gap-2">
            <div className="text-lg font-semibold">{m.value}</div>
            <button
              type="button"
              className="muted inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[11px] leading-none hover:underline"
              style={{ borderColor: "var(--border)" }}
              aria-label={`What does "${m.label}" mean?`}
              aria-expanded={open === m.key}
              title={m.help}
              onClick={() => setOpen(open === m.key ? null : m.key)}
            >?</button>
          </div>
          <div className="muted text-xs">{m.label} <span className="opacity-70">· {m.vs}</span></div>
          {open === m.key && <div className="muted mt-2 border-t pt-2 text-[11px] leading-snug" style={{ borderColor: "var(--border)" }}>{m.help}</div>}
        </div>
      ))}
      <div className="muted -mt-1 text-[11px] sm:col-span-2 lg:col-span-4">Impact figures are illustrative targets for a pilot at this merchant's scale, not measurements. Click ? for the definition.</div>
    </div>
  );
}
