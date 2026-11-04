import { ANON } from "../../lib/brand";
import type { ReactNode } from "react";
import { PARTY_LABEL, PARTY_ORDER, RACI_LEGEND } from "../../lib/bjs/raci";
import type { Party, Raci, RaciLetter } from "../../lib/bjs/types";

export function Pill({ text, tone = "accent", title }: { text: string; tone?: "accent" | "amber" | "muted" | "red"; title?: string }) {
  const style = tone === "muted" ? { background: "var(--border)", color: "var(--muted)" } : tone === "red" ? { background: "#fee2e2", color: "#b91c1c" } : undefined;
  return <span className={`badge ${tone === "amber" ? "badge-amber" : ""}`} style={style} title={title}>{text}</span>;
}

const LETTER_TONE: Record<RaciLetter, "accent" | "amber" | "muted"> = { R: "accent", A: "amber", C: "muted", I: "muted" };

/** RACI chips for one step: who is Responsible, Accountable, Consulted, Informed. Same letters as the Rockler / Alaska narrative. */
export function RaciChips({ raci }: { raci: Raci }) {
  const parties = PARTY_ORDER.filter((p) => raci[p]);
  return (
    <div className="flex flex-wrap gap-1">
      {parties.map((p: Party) => (
        <span key={p} className="inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[11px]" style={{ borderColor: "var(--border)" }} title={RACI_LEGEND[raci[p]!]}>
          <Pill text={raci[p]!} tone={LETTER_TONE[raci[p]!]} />
          <span className="muted">{PARTY_LABEL[p]}</span>
        </span>
      ))}
    </div>
  );
}

export function RaciLegend() {
  return (
    <span className="muted text-xs">
      RACI per step: {(Object.keys(RACI_LEGEND) as RaciLetter[]).map((k, i) => <span key={k}>{i ? " · " : ""}<b>{k}</b> {RACI_LEGEND[k].split(": ")[1]}</span>)}
    </span>
  );
}

export function JsonBlock({ value, maxHeight = 320 }: { value: unknown; maxHeight?: number }) {
  return <pre className="overflow-auto rounded-md p-2 font-mono text-[11px] leading-snug" style={{ background: "var(--bg)", border: "1px solid var(--border)", maxHeight }}>{JSON.stringify(value, null, 2)}</pre>;
}

export function Disclaimer({ product }: { product: "Resolve" | "Check" }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <Pill text={ANON ? "Illustrative demo, stand-in merchant" : "Illustrative demo, not affiliated with BJ's Wholesale Club"} tone="amber" />
      <Pill text="all members, orders and agents are synthetic" tone="muted" />
      <Pill text={`Umpyr ${product}`} />
    </div>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="flex flex-col gap-0.5 text-xs">
      <span className="muted">{label}</span>
      {children}
      {hint && <span className="muted text-[10px]">{hint}</span>}
    </label>
  );
}

export function Hash({ hex, label }: { hex: string; label?: string }) {
  return <div className="muted font-mono text-[11px] break-all">{label ? `${label} ` : ""}{hex}</div>;
}

export const STATUS_TONE: Record<string, string> = { done: "var(--accent)", fired: "var(--accent)", pass: "var(--accent)", waiting: "var(--warn)", needs_fact: "var(--warn)", warn: "var(--warn)", blocked: "#b91c1c", fail: "#b91c1c", skipped: "var(--muted)", info: "var(--muted)" };

export function StatusDot({ status }: { status: string }) {
  return <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: STATUS_TONE[status] ?? "var(--muted)" }} aria-label={status} />;
}
