import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import categoriesJson from "../../../../samples/bjs/categories.json";
import ordersJson from "../../../../samples/bjs/orders.json";
import packJson from "../../../../samples/bjs/rule_pack.json";
import proceduresJson from "../../../../samples/bjs/procedures.json";
import ticketsJson from "../../../../samples/bjs/tickets.json";
import { Disclaimer, Field, Hash, JsonBlock, Pill, RaciChips, RaciLegend, StatusDot } from "../components/bjs/ui";
import { LocalResolveAdapter, type ResolveAdapter } from "../lib/bjs/adapters";
import type { ResolveRun, RuleCheck, RunOptions, RunStep } from "../lib/bjs/resolveEngine";
import type { Category, Claim, Order, Procedure, RulePack, Ticket } from "../lib/bjs/types";

const categories = categoriesJson as Category[];
const orders = ordersJson as unknown as Order[];
const pack = packJson as unknown as RulePack;
const procedures = proceduresJson as unknown as Procedure[];
const tickets = ticketsJson as unknown as Ticket[];

// Adapter boundary: swap for RemoteResolveAdapter when the engine exposes /workspaces/bjs/cases/replay.
const adapter: ResolveAdapter = new LocalResolveAdapter({ pack, procedures, orders, categories });

/** Illustrative impact figures for the header strip. Not measured; targets for a BJ's-scale pilot. */
const IMPACT = [
  { label: "tickets auto-resolved", value: "71%", vs: "vs 0% today" },
  { label: "median time to resolution", value: "3 min 40 s", vs: "vs ~26 h help desk" },
  { label: "cost per contact", value: "$0.42", vs: "vs ~$6.80 seat-based" },
  { label: "agent-channel share", value: "18%", vs: "members' AI agents" },
];

type FieldMeta = { label: string; type: "boolean" | "number" | "text" | "select"; options?: string[] };
const FIELD_META: Record<string, FieldMeta> = {
  reason: { label: "Claim reason", type: "select" },
  requested_remedy: { label: "Requested remedy", type: "select", options: ["refund", "partial_refund", "price_adjustment", "replacement", "redeliver", "none"] },
  has_photos: { label: "Photo provided", type: "boolean" },
  reported_within_hours: { label: "Reported after (hours)", type: "number" },
  quantity_intended: { label: "Units intended", type: "number" },
  quantity_received: { label: "Units received", type: "number" },
  cases_to_keep: { label: "Cases the member keeps", type: "number" },
  coupon_id: { label: "Coupon clipped", type: "text" },
  advertised_price: { label: "Advertised price ($)", type: "number" },
  wants_cancel_membership: { label: "Wants to cancel membership", type: "boolean" },
  used_benefits_since_renewal: { label: "Used benefits since renewal", type: "boolean" },
  hold_age_hours: { label: "Age of pending hold (hours)", type: "number" },
};
const HIDDEN = new Set(["item_id", "description", "item_ids", "requested_amount"]);

function reasonsFor(categoryKey: string): string[] {
  return [...new Set(pack.rules.filter((r) => r.categories.includes(categoryKey)).flatMap((r) => r.claim_reasons))];
}

function FactsEditor({ ticket, run, facts, onChange }: { ticket: Ticket; run: ResolveRun; facts: RunOptions["facts"]; onChange: (f: RunOptions["facts"]) => void }) {
  const claim = run.facts.claim as Claim & Record<string, unknown>;
  const keys = Object.keys(ticket.extracted).filter((k) => !HIDDEN.has(k));
  const set = (k: string, v: unknown) => onChange({ ...facts, [k]: v });
  return (
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
      <Field label="Item">
        <select className="input" value={(facts.item_id ?? ticket.extracted.item_id) as string} onChange={(e) => set("item_id", e.target.value)}>
          {run.order?.items.map((i) => <option key={i.item_id} value={i.item_id}>{i.name}</option>)}
        </select>
      </Field>
      {keys.map((k) => {
        const meta = FIELD_META[k] ?? { label: k, type: typeof claim[k] === "number" ? "number" : typeof claim[k] === "boolean" ? "boolean" : "text" };
        const v = claim[k];
        if (meta.type === "boolean") {
          return (
            <Field key={k} label={meta.label}>
              <select className="input" value={v === null || v === undefined ? "unknown" : v ? "yes" : "no"} onChange={(e) => set(k, e.target.value === "unknown" ? null : e.target.value === "yes")}>
                <option value="unknown">unknown</option><option value="yes">yes</option><option value="no">no</option>
              </select>
            </Field>
          );
        }
        if (meta.type === "select") {
          const opts = k === "reason" ? reasonsFor(ticket.category_key) : meta.options ?? [];
          return <Field key={k} label={meta.label}><select className="input" value={String(v ?? "")} onChange={(e) => set(k, e.target.value)}>{opts.map((o) => <option key={o} value={o}>{o}</option>)}</select></Field>;
        }
        if (meta.type === "number") return <Field key={k} label={meta.label}><input className="input" type="number" value={v === null || v === undefined ? "" : String(v)} onChange={(e) => set(k, e.target.value === "" ? null : Number(e.target.value))} /></Field>;
        return <Field key={k} label={meta.label}><input className="input" value={String(v ?? "")} onChange={(e) => set(k, e.target.value)} /></Field>;
      })}
      <div className="sm:col-span-2 lg:col-span-3 muted text-[11px]">Order, item, member and fulfilment facts come from BJ's record (read-only here): {run.facts.item.name} · {run.order?.fulfilment.method.replace(/_/g, " ")} · member {run.order?.member.tier === "club_plus" ? "Club+" : "Club"} · payments {run.order?.payments ? `${run.order.payments.posted_captures} posted, ${run.order.payments.pending_holds} pending` : "n/a"}.</div>
    </div>
  );
}

function Checks({ checks }: { checks: RuleCheck[] }) {
  return (
    <table className="w-full text-[11px]">
      <thead className="muted text-left"><tr><th className="pr-2">Rule</th><th className="pr-2">Tier</th><th className="pr-2">Result</th><th>Fields</th></tr></thead>
      <tbody>
        {checks.map((c) => {
          const clause = pack.clauses.find((x) => x.clause_id === c.clause_id);
          return (
            <tr key={c.rule_id} className="border-t align-top" style={{ borderColor: "var(--border)" }}>
              <td className="py-1 pr-2">
                <div className="font-mono">{clause?.source_url.startsWith("http") ? <a className="underline" href={clause.source_url} target="_blank" rel="noreferrer" title={clause.text}>{c.rule_id}</a> : <span title={clause?.text}>{c.rule_id}</span>}</div>
                <div className="muted">{c.description}</div>
                {clause && !clause.confirmed && <Pill text="illustrative practice rule" tone="amber" />}
              </td>
              <td className="py-1 pr-2">{c.tier} p{c.priority}</td>
              <td className="py-1 pr-2" style={{ color: c.fired ? "var(--accent)" : c.missing.length ? "var(--warn)" : "var(--muted)" }}>{c.fired ? "fired" : c.missing.length ? `missing ${c.missing.join(", ")}` : "not fired"}</td>
              <td className="py-1 font-mono">{Object.entries(c.fields).map(([k, v]) => <div key={k}>{k} = {v === undefined ? "—" : JSON.stringify(v)}</div>)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function StepBody({ step, run, ticket, opts, setOpts }: { step: RunStep; run: ResolveRun; ticket: Ticket; opts: RunOptions; setOpts: (o: RunOptions) => void }) {
  switch (step.id) {
    case "intake": {
      const masked = (step.data as { masked: unknown }).masked;
      return ticket.channel === "email"
        ? <div className="space-y-1 text-xs">{(masked as { role: string; text: string }[]).map((m, i) => <div key={i} className="rounded-md p-2" style={{ background: "var(--bg)", border: "1px solid var(--border)" }}><span className="muted">{m.role} (masked): </span>{m.text}</div>)}</div>
        : <JsonBlock value={masked} maxHeight={220} />;
    }
    case "order_match": return run.order ? <details className="text-xs"><summary className="muted cursor-pointer">BJ's order record</summary><JsonBlock value={run.order} maxHeight={260} /></details> : null;
    case "facts": return <FactsEditor ticket={ticket} run={run} facts={opts.facts} onChange={(facts) => setOpts({ ...opts, facts })} />;
    case "rule": {
      const win = pack.clauses.find((c) => c.clause_id === run.ruling.winning_clause_id);
      return (
        <div className="space-y-2 text-xs">
          {win && <div className="rounded-md p-2" style={{ background: "var(--bg)", border: "1px solid var(--border)" }}><div className="flex flex-wrap items-center gap-2"><span className="font-mono">{win.clause_id}</span><span className="font-medium">{win.title}</span><Pill text={win.tier} tone="muted" />{win.confirmed ? <a className="underline muted" href={win.source_url} target="_blank" rel="noreferrer">{win.source_title}</a> : <Pill text="illustrative" tone="amber" />}</div><div className="muted mt-1">“{win.text}”</div></div>}
          <details><summary className="muted cursor-pointer">{run.checks.length} applicable rules · pack {run.pack_version}</summary><div className="mt-1 overflow-x-auto"><Checks checks={run.checks} /></div></details>
        </div>
      );
    }
    case "resolution": return run.procedure ? <details className="text-xs"><summary className="muted cursor-pointer">{run.procedure.steps.length} approved steps</summary><ol className="mt-1 space-y-0.5">{run.procedure.steps.map((s) => <li key={s.step_id}><span className="font-mono">{s.step_id}</span> {s.name} <span className="muted">· {s.kind} · {s.tool}{s.clause_ids.length ? ` · ${s.clause_ids.join(", ")}` : ""}</span></li>)}</ol></details> : null;
    case "pdp": return (
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="muted">{run.pdp.reasons.join("; ")}</span>
        {run.pdp.decision === "needs_approval" && !opts.staff_approved && <button className="btn btn-primary" onClick={() => setOpts({ ...opts, staff_approved: true })}>Approve as BJ's staff</button>}
        {opts.staff_approved && <Pill text="approved by BJ's staff" />}
      </div>
    );
    case "terms": {
      const d = step.data as { terms: Record<string, unknown>; hash: string; confirmation: Record<string, unknown> | null };
      return (
        <div className="space-y-2 text-xs">
          <Hash hex={d.hash} label="terms sha256" />
          <details><summary className="muted cursor-pointer">terms the member confirms</summary><JsonBlock value={d.terms} maxHeight={200} /></details>
          {run.terms.status === "awaiting" && <button className="btn btn-primary" onClick={() => setOpts({ ...opts, customer_confirmed: true })}>{ticket.channel === "agent" ? "Agent returns terms_accepted (member approved)" : "Member clicks confirm"}</button>}
          {d.confirmation && <JsonBlock value={d.confirmation} maxHeight={140} />}
        </div>
      );
    }
    case "execute": return run.execution.request ? <div className="grid gap-2 text-xs md:grid-cols-2"><div><div className="muted">request → BJ's</div><JsonBlock value={run.execution.request} maxHeight={200} /></div><div><div className="muted">response ← BJ's</div><JsonBlock value={run.execution.response} maxHeight={200} /></div></div> : null;
    case "audit": return (
      <div className="space-y-1 text-xs">
        <Hash hex={run.audit.hash} label="record sha256" />
        <Hash hex={run.audit.signature} label={`signature hmac-sha256 kid=${run.audit.kid}`} />
        <details><summary className="muted cursor-pointer">audit record</summary><JsonBlock value={run.audit.record} maxHeight={260} /></details>
      </div>
    );
    default: return null;
  }
}

export function BjsResolve() {
  const [categoryKey, setCategoryKey] = useState(categories[0].key);
  const [opts, setOpts] = useState<RunOptions>({ channel: "email", mode: "autonomous", auto_approve_limit: 100, facts: {}, customer_confirmed: false, staff_approved: false });
  const [open, setOpen] = useState<Record<string, boolean>>({ intake: true, facts: true, rule: true, terms: true, execute: true, audit: true });

  const ticket = useMemo(() => tickets.find((t) => t.category_key === categoryKey && t.channel === opts.channel) ?? tickets[0], [categoryKey, opts.channel]);
  const run = useMemo(() => adapter.run(ticket, opts) as ResolveRun, [ticket, opts]);
  const category = categories.find((c) => c.key === categoryKey)!;

  const pick = (key: string) => { setCategoryKey(key); setOpts((o) => ({ ...o, facts: {}, customer_confirmed: false, staff_approved: false })); };
  const setChannel = (channel: "email" | "agent") => setOpts((o) => ({ ...o, channel, facts: {}, customer_confirmed: false, staff_approved: false }));
  const decisionTone = run.ruling.decision === "approve" || run.ruling.decision === "partial" ? "accent" : run.ruling.decision === "deny" ? "amber" : "muted";

  return (
    <div className="flex flex-col gap-3">
      <div className="panel flex flex-wrap items-center gap-3 px-3 py-2 text-sm">
        <span className="font-semibold">BJ's Wholesale Club · Resolve</span>
        <Disclaimer product="Resolve" />
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1"><span className="muted">Channel</span>
            <select className="input !w-auto" value={opts.channel} onChange={(e) => setChannel(e.target.value as "email" | "agent")}><option value="email">Forwarded email</option><option value="agent">Member's AI agent</option></select></label>
          <label className="flex items-center gap-1"><span className="muted">Mode</span>
            <select className="input !w-auto" value={opts.mode} onChange={(e) => setOpts({ ...opts, mode: e.target.value as RunOptions["mode"] })}><option value="autonomous">Autonomous</option><option value="assist">Assist</option><option value="shadow">Shadow</option></select></label>
          <label className="flex items-center gap-1"><span className="muted">Auto-approve ≤ $</span>
            <input className="input !w-24" type="number" min={0} step={25} value={opts.auto_approve_limit} onChange={(e) => setOpts({ ...opts, auto_approve_limit: Number(e.target.value) })} /></label>
          <button className="btn" onClick={() => pick(categoryKey)}>Reset case</button>
        </div>
      </div>

      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {IMPACT.map((m) => (
          <div key={m.label} className="panel px-3 py-2">
            <div className="text-lg font-semibold">{m.value}</div>
            <div className="muted text-xs">{m.label} <span className="opacity-70">· {m.vs}</span></div>
          </div>
        ))}
        <div className="muted text-[11px] sm:col-span-2 lg:col-span-4 -mt-1">Impact figures are illustrative targets for a BJ's-scale pilot, not measurements.</div>
      </div>

      <div className="flex flex-wrap gap-2">
        {categories.map((c) => <button key={c.key} className={`btn text-xs ${c.key === categoryKey ? "btn-primary" : ""}`} onClick={() => pick(c.key)}>{c.name}</button>)}
      </div>

      <div className="grid gap-3 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <div className="space-y-3">
          <section className="panel p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="font-semibold">Ticket</h2>
              <Pill text={ticket.ticket_id} tone="muted" />
              <Pill text={ticket.channel === "email" ? "forwarded email" : `agent · ${ticket.agent_message?.agent.platform}`} tone="muted" />
              <Pill text="synthetic" tone="amber" />
            </div>
            <div className="muted mt-1 text-xs">{category.description}</div>
            <div className="mt-2 font-medium">{ticket.subject}</div>
            {ticket.channel === "email"
              ? ticket.thread?.map((m, i) => <div key={i} className="mt-2 rounded-lg px-3 py-2 text-sm whitespace-pre-wrap" style={{ background: "var(--accent-soft)", border: "1px solid var(--border)" }}><div className="muted mb-1 text-xs">{m.role} · {m.at} · raw (before masking)</div>{m.text}</div>)
              : <div className="mt-2"><div className="muted mb-1 text-xs">protocol {ticket.agent_message?.protocol} · structured claim from the member's agent</div><JsonBlock value={ticket.agent_message} maxHeight={260} /></div>}
          </section>

          <section className="panel p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="font-semibold">Decision card</h2>
              <Pill text={run.ruling.decision} tone={decisionTone} />
              {run.ruling.remedy && <Pill text={`remedy: ${run.ruling.remedy}`} tone="muted" />}
              {run.ruling.amount && <Pill text={`$${run.ruling.amount}`} tone="muted" />}
              <Pill text={`fault: ${run.ruling.fault}`} tone="muted" />
              <Pill text={`PDP: ${run.pdp.decision}`} tone={run.pdp.decision === "allow" ? "accent" : "amber"} />
              {run.ruling.winning_rule_id && <Pill text={run.ruling.winning_rule_id} tone="muted" />}
              <Pill text={`pack ${run.pack_version}`} tone="muted" />
            </div>
            <p className="mt-2 text-xs">{run.ruling.explanation}</p>
            <div className="mt-2">
              <div className="muted mb-1 text-xs">{opts.channel === "agent" ? "Umpyr → member's agent (umpyr-resolve/0.2)" : "Umpyr → member (email reply)"}</div>
              {opts.channel === "agent" ? <JsonBlock value={run.agent_reply} maxHeight={260} /> : <div className="rounded-lg px-3 py-2 text-sm whitespace-pre-wrap" style={{ background: "var(--bg)", border: "1px solid var(--border)" }}>{run.reply}</div>}
            </div>
            <div className="muted mt-2 text-xs"><b>Lifecycle closure (BJ's side):</b> {run.lifecycle_note}</div>
          </section>
          <div className="muted text-xs">Pre-payment risk and intent on agent-placed orders is a separate product: <Link className="underline" to="/bjs/check">Umpyr Check for BJ's →</Link></div>
        </div>

        <aside className="space-y-2 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2 px-1"><h2 className="font-semibold">Pipeline · case {run.case_id}</h2><RaciLegend /></div>
          {run.steps.map((s) => (
            <section key={s.id} className="panel p-3">
              <div className="flex flex-wrap items-start gap-2">
                <StatusDot status={s.status} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="muted font-mono text-xs">{String(s.n).padStart(2, "0")}</span>
                    <span className="font-medium">{s.name}</span>
                    <Pill text={s.status.replace("_", " ")} tone={s.status === "done" || s.status === "fired" ? "accent" : s.status === "waiting" || s.status === "needs_fact" ? "amber" : "muted"} />
                    {(s.id === "intake" || s.id === "facts" || s.id === "rule" || s.id === "terms" || s.id === "execute" || s.id === "audit" || s.id === "order_match" || s.id === "resolution") && (
                      <button className="btn ml-auto !px-2 !py-0.5 text-xs" onClick={() => setOpen({ ...open, [s.id]: !open[s.id] })}>{open[s.id] ? "hide" : "show"}</button>
                    )}
                  </div>
                  <div className="mt-1 text-xs">{s.summary}</div>
                  {s.detail && s.detail !== s.summary && <div className="muted mt-0.5 text-xs">{s.detail}</div>}
                  <div className="mt-1.5"><RaciChips raci={s.raci} /></div>
                  {(open[s.id] || s.id === "pdp") && <div className="mt-2"><StepBody step={s} run={run} ticket={ticket} opts={opts} setOpts={setOpts} /></div>}
                </div>
              </div>
            </section>
          ))}
        </aside>
      </div>
    </div>
  );
}
