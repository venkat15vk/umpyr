import type { Ops } from "../lib/resolve";

const STATUS_COLOR: Record<string, string> = { fired: "var(--accent)", ok: "var(--accent)", ready: "var(--accent)", needs_fact: "var(--warn)", pending: "var(--muted)", evaluated: "var(--muted)", placeholder: "var(--muted)", "n/a": "var(--muted)" };

function Pill({ text, tone = "accent" }: { text: string; tone?: "accent" | "amber" | "muted" }) {
  return <span className={`badge ${tone === "amber" ? "badge-amber" : ""}`} style={tone === "muted" ? { background: "var(--border)", color: "var(--muted)" } : undefined}>{text}</span>;
}

function fmt(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function OpsView({ ops }: { ops?: Ops }) {
  if (!ops) {
    return (
      <aside className="panel p-4 text-sm">
        <h2 className="font-semibold">Ops view</h2>
        <p className="muted mt-2">Every step of the resolution appears here: classification, neighbour cases, the approved procedure with its policy clauses and checks, the ruling, the policy decision point and the audit hash.</p>
      </aside>
    );
  }
  const r = ops.ruling;
  const decisionTone = r.decision === "approve" || r.decision === "partial" ? "accent" : r.decision === "deny" ? "amber" : "muted";
  return (
    <aside className="space-y-3 text-sm">
      <section className="panel p-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-semibold">Classification</h2>
          <Pill text={ops.classification.category_name ?? ops.classification.category_key} />
          <span className="muted">confidence {(ops.classification.confidence * 100).toFixed(0)}% · {ops.classification.source === "router" ? "router" : "LLM fallback (router < 0.70)"}</span>
          {ops.classification.multi_issue && <Pill text={`multi-issue: ${ops.classification.secondary_category_key}`} tone="amber" />}
        </div>
        <div className="muted mt-1 text-xs">{ops.record.found ? `Record ${ops.record.reference}: ${ops.record.summary}` : `No merchant record matched (${ops.record.reference ?? "no reference given"})`}</div>
      </section>

      <section className="panel p-3">
        <h2 className="font-semibold">Neighbour cases</h2>
        <ul className="mt-1 space-y-1">
          {ops.neighbours.map((n) => (
            <li key={n.ticket_id} className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs">{n.ticket_id}</span>
              <span className="muted text-xs">{(n.similarity * 100).toFixed(0)}%</span>
              <span className="truncate">{n.summary}</span>
              {n.staff_outcome && <Pill text={`${fmt((n.staff_outcome as Record<string, unknown>).decision)}/${fmt((n.staff_outcome as Record<string, unknown>).remedy)}`} tone="muted" />}
              {n.is_drift && <Pill text="drift" tone="amber" />}
            </li>
          ))}
          {ops.neighbours.length === 0 && <li className="muted">none</li>}
        </ul>
      </section>

      <section className="panel p-3">
        <div className="flex items-center gap-2">
          <h2 className="font-semibold">Procedure</h2>
          <Pill text={ops.procedure.ref ?? "none"} />
          <span className="muted">{ops.procedure.name}</span>
        </div>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="muted text-left"><tr><th className="pr-2">Step</th><th className="pr-2">Policy clause</th><th className="pr-2">Ontology fields</th><th>Check</th></tr></thead>
            <tbody>
              {ops.procedure.steps.map((s) => (
                <tr key={s.step_id} className="border-t align-top" style={{ borderColor: "var(--border)" }}>
                  <td className="py-1 pr-2"><div>{s.name}</div><div className="muted">{s.kind} · {s.tool}</div></td>
                  <td className="py-1 pr-2">{s.clause_ids.length ? s.clause_ids.map((c) => <div key={c} title={s.clauses[c]}><span className="font-mono">{c}</span></div>) : <span className="muted">—</span>}</td>
                  <td className="py-1 pr-2">{Object.keys(s.fields).length ? Object.entries(s.fields).map(([k, v]) => <div key={k}><span className="font-mono">{k}</span> = {fmt(v)}</div>) : <span className="muted">—</span>}</td>
                  <td className="py-1"><span style={{ color: STATUS_COLOR[s.status] ?? "inherit" }}>{s.status}</span><div className="muted">{s.detail}</div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel p-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-semibold">Ruling</h2>
          <Pill text={r.decision ?? "none"} tone={decisionTone} />
          {r.remedy && <Pill text={`remedy: ${r.remedy}`} tone="muted" />}
          {r.amount && <Pill text={`$${r.amount}`} tone="muted" />}
          {r.fault && <Pill text={`fault: ${r.fault}`} tone="muted" />}
          {ops.authorization && <Pill text={`authority: ${ops.authorization.decision}`} tone={ops.authorization.decision === "allow" ? "accent" : "amber"} />}
        </div>
        <p className="mt-1">{r.explanation}</p>
        {r.missing_facts && r.missing_facts.length > 0 && <p className="mt-1"><span className="muted">Asking for:</span> {r.missing_facts.join(", ")}</p>}
        {r.conflicts && r.conflicts.length > 0 && <p className="mt-1 text-amber-600">Conflict: {r.conflicts.join("; ")}</p>}
        {ops.authorization && <p className="muted mt-1 text-xs">PDP: {ops.authorization.action} → {ops.authorization.decision} ({ops.authorization.reasons.join("; ")})</p>}
        {r.clause_notes.length > 0 && (
          <ul className="mt-2 space-y-1 text-xs">
            {r.clause_notes.map((c) => <li key={c.clause_id}><span className="font-mono">{c.clause_id}</span> <span className="muted">{c.title}:</span> “{c.text}”</li>)}
          </ul>
        )}
        {ops.checks.length > 0 && (
          <details className="mt-2 text-xs"><summary className="muted cursor-pointer">{ops.checks.length} rule checks</summary>
            <ul className="mt-1 space-y-0.5">{ops.checks.map((c) => <li key={c.rule_id}><span className="font-mono">{c.rule_id}</span> <span className="muted">{c.tier} p{c.priority}</span> → {c.fired ? "fired" : c.missing.length ? `missing ${c.missing.join(", ")}` : "not fired"}</li>)}</ul>
          </details>
        )}
      </section>

      <section className="panel p-3">
        <h2 className="font-semibold">Governance</h2>
        <div className="mt-1 flex flex-wrap gap-2 text-xs">
          <Pill text={ops.governance.pii_masked ? "PII masked" : "PII not masked"} />
          <Pill text={`policy ${ops.governance.rule_pack_version ?? "—"}`} tone="muted" />
          <Pill text={`procedure ${ops.governance.procedure ?? "—"}`} tone="muted" />
          <Pill text={ops.governance.authority} tone="muted" />
          <Pill text={`supervisor ${ops.governance.supervisor_check}`} tone={ops.governance.supervisor_check === "passed" ? "accent" : "amber"} />
          {ops.governance.is_synthetic && <Pill text="synthetic" tone="amber" />}
        </div>
        <div className="muted mt-1 font-mono text-xs break-all">audit sha256 {ops.governance.audit_hash}</div>
        {ops.supervisor.issues.length > 0 && <div className="mt-1 text-xs text-amber-600">Supervisor notes: {ops.supervisor.issues.join("; ")}{ops.supervisor.retried ? " (reply regenerated)" : ""}</div>}
        <details className="mt-2 text-xs"><summary className="muted cursor-pointer">tool trace</summary>
          <ol className="mt-1 space-y-0.5">{ops.tools.map((t, i) => <li key={i}><span className="font-mono">{t.tool}</span> <span className="muted">{t.detail}</span></li>)}</ol>
        </details>
      </section>
    </aside>
  );
}
