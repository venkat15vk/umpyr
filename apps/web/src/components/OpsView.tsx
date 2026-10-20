import type { Citation, Ops } from "../lib/resolve";

const STATUS_COLOR: Record<string, string> = { fired: "var(--accent)", ok: "var(--accent)", ready: "var(--accent)", needs_fact: "var(--warn)", pending: "var(--muted)", evaluated: "var(--muted)", placeholder: "var(--muted)", "n/a": "var(--muted)" };

function Pill({ text, tone = "accent" }: { text: string; tone?: "accent" | "amber" | "muted" }) {
  return <span className={`badge ${tone === "amber" ? "badge-amber" : ""}`} style={tone === "muted" ? { background: "var(--border)", color: "var(--muted)" } : undefined}>{text}</span>;
}

function where(c: Citation): string {
  return [c.document, c.section ? `§ ${c.section}` : null, c.page ? `p.${c.page}` : null, c.document_version ? `v${c.document_version}` : null].filter(Boolean).join(" · ");
}

/** A rule chip that links to the source document; hover shows the clause text. Citations come from rule provenance, never from a model. */
function Cite({ id, cit, fallback }: { id: string; cit?: Citation; fallback?: string }) {
  if (!cit) return <div title={fallback}><span className="font-mono">{id}</span></div>;
  return (
    <div title={cit.quote}>
      {cit.url && !cit.url.startsWith("practice://") ? <a className="font-mono underline" href={cit.url} target="_blank" rel="noreferrer">{id}</a> : <span className="font-mono">{id}</span>}
      <span className="muted"> {where(cit)}{cit.public ? "" : " · internal"}</span>
    </div>
  );
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
  const d = ops.turn_delta;
  return (
    <aside className="space-y-3 text-sm">
      {d && d.turn > 1 && (
        <section className="panel p-3">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-semibold">This turn</h2>
            <Pill text={`turn ${d.turn}`} tone="muted" />
            {d.message_category && <Pill text={`message: ${d.message_category.category_key ?? "?"} ${(d.message_category.confidence * 100).toFixed(0)}%`} tone={d.message_category.new_category ? "amber" : "muted"} />}
            {d.intents_added.length > 0 && <Pill text={`new ask: ${d.intents_added.join(", ")}`} tone="amber" />}
            <Pill text={d.ruling_changed ? "ruling changed" : "ruling unchanged"} tone={d.ruling_changed ? "accent" : "muted"} />
            {d.pushback && <Pill text="pushback on a denial" tone="amber" />}
          </div>
          <div className="muted mt-1 text-xs">{d.facts_changed.length ? `facts changed: ${d.facts_changed.join(", ")}` : "no facts changed"} · plan {d.plan_comparison} · {d.status_before ?? "new"} → {d.status}</div>
          {d.precedent && (
            <div className="mt-1 text-xs">
              Precedent for goodwill where policy denied: <span className="font-mono">{d.precedent.count}/{d.precedent.denied}</span> similar cases ({Object.entries(d.precedent.remedies).map(([k, v]) => `${k} ×${v}`).join(", ") || "none"}) → {d.precedent.exists ? "handed to a person with the precedent" : "no precedent; policy stands"}
              {d.precedent.examples.length > 0 && <ul className="muted mt-1">{d.precedent.examples.map((e) => <li key={e.ticket_id}><span className="font-mono">{e.ticket_id}</span> {e.remedy}{e.amount ? ` $${e.amount}` : ""}: {e.summary}</li>)}</ul>}
            </div>
          )}
          {d.uncovered_ask && <div className="muted mt-1 text-xs">Ask not covered by policy ({d.uncovered_ask.intents.join(", ")}); reply offers a specialist review.</div>}
        </section>
      )}
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

      {ops.plan && (
        <section className="panel p-3">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-semibold">Plan</h2>
            <Pill text={ops.plan.comparison.kind.replace("_", " ")} tone={ops.plan.disagreement ? "amber" : ops.plan.comparison.kind === "agree" ? "accent" : "muted"} />
            <Pill text={`chosen: ${ops.plan.chosen}`} tone="muted" />
            {ops.plan.novel && <Pill text="novel case" tone="amber" />}
            {ops.plan.comparison.kind === "diverge" && !ops.plan.disagreement && <Pill text="history not confident: live plan used" tone="muted" />}
          </div>
          <div className="mt-1 grid gap-2 text-xs sm:grid-cols-2">
            <div>
              <div className="muted">History · {ops.plan.historical.k} neighbours · sim {ops.plan.historical.mean_similarity === null ? "—" : `${(ops.plan.historical.mean_similarity * 100).toFixed(0)}%`} · agreement {(ops.plan.historical.agreement * 100).toFixed(0)}%</div>
              <div className="font-mono">{ops.plan.historical.steps.join(" → ") || "—"}</div>
            </div>
            <div>
              <div className="muted">Live planner (Haiku, fixed vocabulary)</div>
              <div className="font-mono">{ops.plan.live.steps.join(" → ") || "—"}</div>
              {ops.plan.live.rationale && <div className="muted">{ops.plan.live.rationale}</div>}
            </div>
          </div>
          {ops.plan.disagreement && <p className="mt-1 text-xs text-amber-600">Diverges: history has {ops.plan.comparison.missing_in_live.join(", ") || "nothing extra"}; live adds {ops.plan.comparison.extra_in_live.join(", ") || "nothing"}.</p>}
          <div className="muted mt-1 text-xs break-all">embedded as: {ops.plan.case_text}</div>
        </section>
      )}

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
                  <td className="py-1 pr-2">{s.clause_ids.length ? s.clause_ids.map((c) => <Cite key={c} id={c} cit={ops.citations?.[c]} fallback={s.clauses[c]} />) : <span className="muted">—</span>}</td>
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
          {r.escalation && <Pill text={`escalation: ${r.escalation.type.replace("_", " ")}`} tone="amber" />}
          {r.gap && <Pill text={`ask: ${r.gap.type.replace("_", " ")}`} tone="muted" />}
        </div>
        <p className="mt-1">{r.explanation}</p>
        {r.missing_facts && r.missing_facts.length > 0 && <p className="mt-1"><span className="muted">Asking for:</span> {r.missing_facts.join(", ")}</p>}
        {r.conflicts && r.conflicts.length > 0 && <p className="mt-1 text-amber-600">Conflict: {r.conflicts.join("; ")}</p>}
        {ops.authorization && <p className="muted mt-1 text-xs">PDP: {ops.authorization.action} → {ops.authorization.decision} ({ops.authorization.reasons.join("; ")})</p>}
        {r.escalation && <p className="muted mt-1 text-xs">Escalation reason: {r.escalation.reason}</p>}
        {r.citations && r.citations.length > 0 ? (
          <ul className="mt-2 space-y-1 text-xs">
            {r.citations.map((c) => (
              <li key={c.clause_id}>
                <Cite id={c.clause_id} cit={c} />
                <div className="muted">{c.title}: “{c.quote}”</div>
              </li>
            ))}
          </ul>
        ) : r.clause_notes.length > 0 && (
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
          {ops.governance.check && <Pill text={`check ${ops.governance.check.replace("_", " ")}`} tone="muted" />}
          {ops.case && <Pill text={`case cost $${Number(ops.case.cost_usd).toFixed(4)} · ${ops.case.llm_calls} calls`} tone="muted" />}
        </div>
        <div className="muted mt-1 font-mono text-xs break-all">audit sha256 {ops.governance.audit_hash}</div>
        {ops.case && <div className="muted font-mono text-xs break-all">ledger {ops.case.event_count} events · head {ops.case.head_hash?.slice(0, 16)}… · decision {ops.case.decision_hash?.slice(0, 16)}…</div>}
        {ops.supervisor.issues.length > 0 && <div className="mt-1 text-xs text-amber-600">Supervisor notes: {ops.supervisor.issues.join("; ")}{ops.supervisor.retried ? " (reply regenerated)" : ""}</div>}
        {ops.status_events && ops.status_events.length > 0 && (
          <details className="mt-2 text-xs"><summary className="muted cursor-pointer">customer status timeline ({ops.status_events.length})</summary>
            <ol className="mt-1 space-y-0.5">{ops.status_events.map((e, i) => <li key={i}><span className="font-mono">{e.action}</span> <span className="muted">{e.message}</span></li>)}</ol>
          </details>
        )}
        <details className="mt-2 text-xs"><summary className="muted cursor-pointer">tool trace</summary>
          <ol className="mt-1 space-y-0.5">{ops.tools.map((t, i) => <li key={i}><span className="font-mono">{t.tool}</span> <span className="muted">{t.detail}</span></li>)}</ol>
        </details>
      </section>
    </aside>
  );
}
