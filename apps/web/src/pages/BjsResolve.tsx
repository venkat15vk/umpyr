import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import agentCasesJson from "../../../../samples/bjs/agent_cases.json";
import categoriesJson from "../../../../samples/bjs/categories.json";
import scenariosJson from "../../../../samples/bjs/check_scenarios.json";
import historyJson from "../../../../samples/bjs/history.json";
import ordersJson from "../../../../samples/bjs/orders.json";
import packJson from "../../../../samples/bjs/rule_pack.json";
import proceduresJson from "../../../../samples/bjs/procedures.json";
import ticketsJson from "../../../../samples/bjs/tickets.json";
import { MetricsStrip } from "../components/MetricsStrip";
import { OpsView } from "../components/OpsView";
import { EvidencePanel, RulingPanel, StatePanel, Transcript } from "../components/bjs/Negotiation";
import { Field, Hash, JsonBlock, Pill, RaciChips, RaciLegend } from "../components/bjs/ui";
import { customerTurn, emptyState, merchantAnswer, rerun, toOps, type ChatState, type Ctx, type TurnResult } from "../lib/bjs/chat";
import type { CheckScenario } from "../lib/bjs/checkLink";
import { negotiate, type AgentCasesFile, type NegotiationCtx } from "../lib/bjs/negotiation";
import { ADAPTERS, adapterById, adapterTrace, type AdapterId } from "../lib/bjs/protocol";
import type { ResolveRun, RunOptions } from "../lib/bjs/resolveEngine";
import type { Category, Claim, ConfirmationBy, Order, Procedure, RulePack, Ticket } from "../lib/bjs/types";
import type { CustomerBlock, Ops } from "../lib/resolve";

const categories = categoriesJson as Category[];
const orders = ordersJson as unknown as Order[];
const pack = packJson as unknown as RulePack;
const procedures = proceduresJson as unknown as Procedure[];
const tickets = ticketsJson as unknown as Ticket[];
const scenarios = (scenariosJson as { scenarios: CheckScenario[] }).scenarios;
const agentCases = (agentCasesJson as unknown as AgentCasesFile).cases;
// Same ctx the FastAPI engine would hold for workspace "bjs"; the deterministic runCase sits behind lib/bjs/adapters.ts.
const ctx: Ctx = { pack, procedures, orders, categories, history: historyJson as unknown as Ctx["history"] };
const nctx: NegotiationCtx = { ...ctx, scenarios };

type Mode = RunOptions["mode"];
type Message = { role: "customer" | "assistant"; text: string; ops?: Ops | null; customer?: CustomerBlock | null; run?: ResolveRun | null };

type FieldMeta = { label: string; type: "boolean" | "number" | "text" | "select"; options?: string[] };
const FIELD_META: Record<string, FieldMeta> = {
  reason: { label: "Claim reason", type: "select" },
  requested_remedy: { label: "Requested remedy", type: "select", options: ["refund", "partial_refund", "price_adjustment", "replacement", "redeliver", "store_credit", "none"] },
  has_photos: { label: "Photo provided", type: "boolean" },
  reported_within_hours: { label: "Reported after (hours)", type: "number" },
  quantity_intended: { label: "Units intended", type: "number" },
  quantity_received: { label: "Units received", type: "number" },
  cases_to_keep: { label: "Cases the member keeps", type: "number" },
  lines_affected: { label: "Lines missing or substituted", type: "number" },
  coupon_id: { label: "Coupon clipped", type: "text" },
  advertised_price: { label: "Advertised price ($)", type: "number" },
  wants_cancel_membership: { label: "Wants to cancel membership", type: "boolean" },
  used_benefits_since_renewal: { label: "Used benefits since renewal", type: "boolean" },
  hold_age_hours: { label: "Age of pending hold (hours)", type: "number" },
};
const HIDDEN = new Set(["item_id", "description", "item_ids", "requested_amount"]);
const reasonsFor = (categoryKey: string) => [...new Set(pack.rules.filter((r) => r.categories.includes(categoryKey)).flatMap((r) => r.claim_reasons))];

/** Editable claim facts: changing one re-runs the ruling (the engine's "fact extraction is editable" contract). */
function FactsEditor({ run, facts, onChange }: { run: ResolveRun; facts: RunOptions["facts"]; onChange: (f: RunOptions["facts"]) => void }) {
  const claim = run.facts.claim as Claim & Record<string, unknown>;
  const required = [...new Set(pack.rules.filter((r) => r.categories.includes(run.ticket.category_key)).flatMap((r) => r.requires).filter((p) => p.startsWith("claim.")).map((p) => p.slice(6)))];
  const keys = [...new Set([...Object.keys(run.ticket.extracted), ...required])].filter((k) => !HIDDEN.has(k));
  const set = (k: string, v: unknown) => onChange({ ...facts, [k]: v });
  return (
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
      <Field label="Item">
        <select className="input" value={(facts.item_id ?? run.ticket.extracted.item_id) as string} onChange={(e) => set("item_id", e.target.value)}>
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
          const opts = k === "reason" ? reasonsFor(run.ticket.category_key) : meta.options ?? [];
          return <Field key={k} label={meta.label}><select className="input" value={String(v ?? "")} onChange={(e) => set(k, e.target.value)}>{opts.map((o) => <option key={o} value={o}>{o}</option>)}</select></Field>;
        }
        if (meta.type === "number") return <Field key={k} label={meta.label}><input className="input" type="number" value={v === null || v === undefined ? "" : String(v)} onChange={(e) => set(k, e.target.value === "" ? null : Number(e.target.value))} /></Field>;
        return <Field key={k} label={meta.label}><input className="input" value={String(v ?? "")} onChange={(e) => set(k, e.target.value)} /></Field>;
      })}
      <div className="muted text-[11px] sm:col-span-2 lg:col-span-3">Order, item, member and fulfilment facts come from BJ's record (read-only): {run.facts.item.name} · {run.order?.fulfilment.method.replace(/_/g, " ")} · member {run.order?.member.tier === "club_plus" ? "Club+" : "Club"} · payments {run.order?.payments ? `${run.order.payments.posted_captures} posted, ${run.order.payments.pending_holds} pending` : "n/a"}.</div>
    </div>
  );
}

const FAULT_TEXT: Record<string, string> = {
  customer: "the member",
  agent: "the member's AI agent (acted outside the member's instruction)",
  agent_platform: "the agent platform (attestation, protocol or mandate handling)",
  merchant: "BJ's (club, warehouse or systems)",
  third_party: "a third party (delivery partner, carrier or payment processor)",
  indeterminate: "not determinable from the facts on file",
};

/** BJ's-specific blocks inside the Ruling section: fault, editable facts, terms hash, execution, lifecycle closure. */
function RulingExtra({ run, state, onFacts, onStaff, onConfirm }: { run: ResolveRun; state: ChatState; onFacts: (f: RunOptions["facts"]) => void; onStaff: () => void; onConfirm: () => void }) {
  const win = pack.clauses.find((c) => c.clause_id === run.ruling.winning_clause_id);
  return (
    <div className="mt-2 space-y-2 border-t pt-2 text-xs" style={{ borderColor: "var(--border)" }}>
      <div><span className="muted">Fault:</span> <b>{run.ruling.fault}</b> · {FAULT_TEXT[run.ruling.fault]}</div>
      {run.order?.check && <div><span className="muted">Check record on this order:</span> {run.order.check.decision.replace("_", "-")} · intent {run.order.check.intent_outcome.replace("_", " ")} · step-up answered by {run.order.check.confirmation_by.replace("_", " ")}</div>}
      {win && !win.confirmed && <div><Pill text="illustrative practice rule" tone="amber" /> <span className="muted">{win.clause_id} was not read on bjs.com; it stands in for a practice BJ's would confirm.</span></div>}
      <details>
        <summary className="muted cursor-pointer">extracted facts (edit to re-run the ruling)</summary>
        <div className="mt-2"><FactsEditor run={run} facts={state.overrides} onChange={onFacts} /></div>
      </details>
      {run.pdp.decision === "needs_approval" && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="muted">Policy decision point: {run.pdp.reasons.join("; ")}</span>
          {!state.staff_approved ? <button className="btn btn-primary" onClick={onStaff}>Approve as BJ's staff</button> : <Pill text="approved by BJ's staff" />}
        </div>
      )}
      {run.terms.status !== "n/a" && (
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="muted">Terms the member confirms</span>
            <Pill text={run.terms.status === "confirmed" ? "confirmed" : "awaiting confirmation"} tone={run.terms.status === "confirmed" ? "accent" : "amber"} />
            {run.terms.status === "awaiting" && run.execution.status !== "waiting_approval" && <button className="btn !px-2 !py-0.5" onClick={onConfirm}>Member clicks confirm</button>}
          </div>
          <Hash hex={run.terms.hash} label="terms sha256" />
          <details><summary className="muted cursor-pointer">terms</summary><JsonBlock value={run.terms.terms} maxHeight={200} /></details>
          {run.terms.confirmation && <details><summary className="muted cursor-pointer">confirmation</summary><JsonBlock value={run.terms.confirmation} maxHeight={140} /></details>}
          <div className="muted">Nothing settles before the member confirms this hash. Money never touches Umpyr.</div>
        </div>
      )}
      {run.execution.status !== "none" && (
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2"><span className="muted">Execution (mock BJ's API)</span><Pill text={run.execution.status.replace("_", " ")} tone={run.execution.status === "executed" ? "accent" : run.execution.status === "shadow" ? "muted" : "amber"} /></div>
          {run.execution.request && (
            <div className="grid gap-2 md:grid-cols-2">
              <div><div className="muted">request → BJ's</div><JsonBlock value={run.execution.request} maxHeight={200} /></div>
              <div><div className="muted">response ← BJ's</div><JsonBlock value={run.execution.response} maxHeight={200} /></div>
            </div>
          )}
        </div>
      )}
      <div><span className="muted">Lifecycle closure (BJ's side):</span> {run.lifecycle_note}</div>
    </div>
  );
}

/** BJ's-specific blocks inside Governance: the signature over the audit record and RACI per pipeline step. */
function GovernanceExtra({ run }: { run: ResolveRun }) {
  return (
    <div className="mt-2 space-y-2 border-t pt-2 text-xs" style={{ borderColor: "var(--border)" }}>
      <Hash hex={run.audit.signature} label={`signature hmac-sha256 kid=${run.audit.kid}`} />
      <details><summary className="muted cursor-pointer">signed audit record</summary><JsonBlock value={run.audit.record} maxHeight={260} /></details>
      <details>
        <summary className="muted cursor-pointer">RACI per pipeline step ({run.steps.length})</summary>
        <div className="muted mt-1"><RaciLegend /></div>
        <table className="mt-1 w-full">
          <tbody>
            {run.steps.map((s) => (
              <tr key={s.id} className="border-t align-top" style={{ borderColor: "var(--border)" }}>
                <td className="py-1 pr-2 whitespace-nowrap"><span className="muted font-mono">{String(s.n).padStart(2, "0")}</span> {s.name}</td>
                <td className="py-1"><RaciChips raci={s.raci} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
      <div className="muted">Before payment, the same order went through <Link className="underline" to="/bjs/check">Check (Part 1) →</Link></div>
    </div>
  );
}

const KIND_BADGE: Record<string, string> = { converge: "agents converge", hold_firm: "Umpyr holds firm", ceiling: "above the ceiling: a person approves", policy_gap: "no clause: a person decides" };

/** Agent channel: a Muse complaint over the adapter, negotiated turn by turn by Umpyr's agent for BJ's. */
function AgentChannel({ caseId, onCase, mode, limit, adapter }: { caseId: string; onCase: (id: string) => void; mode: Mode; limit: number; adapter: AdapterId }) {
  const c = agentCases.find((x) => x.case_id === caseId) ?? agentCases[0];
  const [shown, setShown] = useState(2);
  const [staffApproved, setStaffApproved] = useState(false);
  const [whatIf, setWhatIf] = useState<ConfirmationBy | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const n = useMemo(() => negotiate(c, nctx, { auto_approve_limit: limit, mode, staff_approved: staffApproved, confirmation_by: whatIf, adapter }), [c, limit, mode, staffApproved, whatIf, adapter]);
  const total = n.turns.length;
  const visible = Math.min(shown, total);
  const visibleState = n.turns[visible - 1]?.state ?? n.final;
  const restart = (id?: string) => { if (id) onCase(id); setShown(2); setStaffApproved(false); setWhatIf(null); };
  useEffect(() => { if (typeof endRef.current?.scrollIntoView === "function") endRef.current.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, [visible]);

  const lastUmpyr = [...n.turns.slice(0, visible)].reverse().find((t) => t.speaker === "umpyr" && t.runs);
  const leadKey = c.issues.find((i) => lastUmpyr?.runs?.[i.key])?.key;
  const leadRun = leadKey ? lastUmpyr!.runs![leadKey] : null;
  const umpyrTurns = n.turns.slice(0, visible).filter((t) => t.speaker === "umpyr" && t.runs);
  const prevRun = umpyrTurns.length > 1 && leadKey ? umpyrTurns[umpyrTurns.length - 2].runs?.[leadKey] ?? null : null;
  const ops = leadRun ? toOps(leadRun, ctx, { turn: umpyrTurns.length, prev: prevRun, factsChanged: [], messageCategory: null, pushback: false, classification: { category_key: leadRun.ticket.category_key, confidence: 0.93, source: "router", probabilities: {}, secondary: null } }) : undefined;
  const approvalPending = visibleState.termination?.kind === "escalate_staff" && visibleState.termination.reason.startsWith("approval") && !staffApproved;
  const ad = adapterById(adapter);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {agentCases.map((x) => <button key={x.case_id} className={`btn text-xs ${x.case_id === c.case_id ? "btn-primary" : ""}`} onClick={() => restart(x.case_id)}>{x.title}</button>)}
      </div>
      <div className="muted flex flex-wrap items-center gap-2 px-1 text-xs"><Pill text={KIND_BADGE[c.kind]} tone="amber" /><span>{c.tagline}</span></div>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-3 lg:grid-cols-[minmax(0,6fr)_minmax(0,6fr)]">
        <section className="panel flex min-h-[70vh] min-w-0 flex-col">
          <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2 text-sm" style={{ borderColor: "var(--border)" }}>
            <h2 className="font-semibold">Agent-to-agent negotiation</h2>
            <Pill text={`${ad.short} → umpyr-resolve/0.2`} tone="muted" />
            <span className="muted text-xs">order {c.order_id}</span>
            <div className="ml-auto flex items-center gap-2">
              <button className="btn !px-2 !py-0.5 text-xs" disabled={visible >= total} onClick={() => setShown(visible + 1)}>Next turn</button>
              <button className="btn !px-2 !py-0.5 text-xs" disabled={visible >= total} onClick={() => setShown(total)}>Play all</button>
              <button className="btn !px-2 !py-0.5 text-xs" onClick={() => restart()}>Restart</button>
            </div>
          </div>
          <div className="flex-1 space-y-3 overflow-y-auto p-3">
            <details className="muted rounded-md border border-dashed px-2 py-1 text-[11px]" style={{ borderColor: "var(--border)" }}>
              <summary className="cursor-pointer">Arrived over {ad.name} ({ad.transport}); swap the adapter and nothing below changes</summary>
              <ol className="mt-1 space-y-0.5">{adapterTrace(ad, "resolve").map((s) => <li key={s.step}><b>{s.step}</b> · {s.detail}</li>)}</ol>
            </details>
            <Transcript turns={n.turns} shown={visible} />
            {approvalPending && (
              <div className="mx-auto flex max-w-[90%] flex-wrap items-center gap-2 rounded-lg border border-dashed px-3 py-2 text-xs" style={{ borderColor: "var(--warn)" }}>
                <span>BJ's approver: ${visibleState.current_offer?.total ?? ""} is above the auto-approve ceiling (${limit.toFixed(2)}).</span>
                <button className="btn btn-primary !px-2 !py-0.5" onClick={() => { setStaffApproved(true); setShown(visible + 1); }}>Approve as BJ's staff</button>
                <span className="muted">or raise the ceiling in the header</span>
              </div>
            )}
            {visible >= total && <div className="muted text-center text-xs">end of transcript · {n.umpyr_turn_count} Umpyr turns computed from pack {pack.version}, no model calls</div>}
            <div ref={endRef} />
          </div>
        </section>
        <div className="space-y-3">
          <StatePanel state={visibleState} shown={visible} total={total} />
          <EvidencePanel check={n.check} order={n.order} whatIf={whatIf} onWhatIf={(v) => { setWhatIf(v); setShown(2); setStaffApproved(false); }} />
          <RulingPanel n={n} upTo={visible} />
        </div>
      </div>
      <details className="panel px-3 py-2 text-sm">
        <summary className="cursor-pointer font-semibold">Ops view for the lead issue{leadKey ? `: ${c.issues.find((i) => i.key === leadKey)?.label}` : ""} <span className="muted font-normal text-xs">· classification, neighbour cases, plan, procedure, ruling, governance</span></summary>
        <div className="mt-3"><OpsView ops={ops} planner="procedure library, deterministic" rulingExtra={leadRun ? <div className="muted mt-2 border-t pt-2 text-xs" style={{ borderColor: "var(--border)" }}>Fault: <b>{leadRun.ruling.fault}</b> · {FAULT_TEXT[leadRun.ruling.fault]}. Lifecycle closure (BJ's side): {leadRun.lifecycle_note}</div> : null} /></div>
      </details>
    </div>
  );
}

export function BjsResolve() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as "agent" | "human" | "merchant" | null) ?? "agent";
  const caseId = params.get("case") ?? agentCases[0].case_id;
  const setTab = (t: string) => setParams((p) => { const n = new URLSearchParams(p); n.set("tab", t); return n; }, { replace: true });
  const setCase = (id: string) => setParams((p) => { const n = new URLSearchParams(p); n.set("case", id); n.set("tab", "agent"); return n; }, { replace: true });

  const [mode, setMode] = useState<Mode>("autonomous");
  const [limit, setLimit] = useState(100);
  const [adapter, setAdapter] = useState<AdapterId>("pap-mcp");
  const opts = useMemo(() => ({ channel: "email" as const, mode, auto_approve_limit: limit }), [mode, limit]);

  // human channel (forwarded email), unchanged from the first BJ's Resolve page
  const [state, setState] = useState<ChatState>(emptyState());
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [merchantQ, setMerchantQ] = useState("");
  const [merchantLog, setMerchantLog] = useState<{ q: string; a: string }[]>([]);
  const endRef = useRef<HTMLDivElement>(null);
  const examples = useMemo(() => tickets.filter((t) => t.channel === "email"), []);
  useEffect(() => { if (typeof endRef.current?.scrollIntoView === "function") endRef.current.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, [messages.length]);
  const start = () => { setState(emptyState()); setMessages([]); setInput(""); };
  const apply = (r: TurnResult, replaceLast: boolean) => {
    setState(r.state);
    const msg: Message = { role: "assistant", text: r.reply, ops: r.ops, customer: r.customer, run: r.run };
    setMessages((m) => {
      if (replaceLast) {
        const idx = [...m].reverse().findIndex((x) => x.role === "assistant");
        if (idx >= 0) { const copy = [...m]; copy[m.length - 1 - idx] = msg; return copy; }
      }
      return [...m, msg];
    });
  };
  const send = (text: string, example?: Ticket) => {
    if (!text.trim()) return;
    setInput("");
    setMessages((m) => [...m, { role: "customer", text }]);
    apply(customerTurn(state, text, opts, ctx, example), false);
  };
  const rerunWith = (patch: Partial<ChatState>, nextOpts = opts) => {
    const s = { ...state, ...patch };
    const r = rerun(s, nextOpts, ctx);
    if (r) apply(r, true); else setState(s);
  };
  const setOption = (patch: { mode?: Mode; auto_approve_limit?: number }) => {
    const next = { ...opts, ...patch };
    if (patch.mode) setMode(patch.mode);
    if (patch.auto_approve_limit !== undefined) setLimit(patch.auto_approve_limit);
    if (state.ticket) rerunWith({}, next);
  };
  const ask = () => {
    if (!merchantQ.trim()) return;
    const q = merchantQ;
    setMerchantQ("");
    setMerchantLog((l) => [...l, { q, a: merchantAnswer(q, ctx, tickets) }]);
  };
  const last = [...messages].reverse().find((m) => m.ops);
  const lastOps = last?.ops ?? undefined;
  const lastRun = last?.run ?? null;

  return (
    <div className="flex flex-col gap-3">
      <div className="panel flex flex-wrap items-center gap-3 px-3 py-2 text-sm">
        <span className="font-semibold">BJ's Wholesale Club</span>
        <span className="badge">Muse order lifecycle · Part 2: Resolve (after the order)</span>
        <span className="badge badge-amber">synthetic data</span>
        <span className="badge badge-amber" title="Illustrative demo, not affiliated with BJ's Wholesale Club. BJ's is not live on Muse.">illustrative · not affiliated · BJ's is not live on Muse</span>
        <label className="flex items-center gap-1">
          <span className="muted">Protocol adapter</span>
          <select className="input !w-auto" value={adapter} onChange={(e) => setAdapter(e.target.value as AdapterId)} title="Swappable: the adapter is the only code that knows the wire protocol">
            {ADAPTERS.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1">
          <span className="muted">Mode</span>
          <select className="input !w-auto" value={mode} onChange={(e) => setOption({ mode: e.target.value as Mode })}>
            <option value="autonomous">Autonomous</option>
            <option value="assist">Assist</option>
            <option value="shadow">Shadow</option>
          </select>
        </label>
        <label className="flex items-center gap-1">
          <span className="muted">Auto-approve ≤ $</span>
          <input className="input !w-24" type="number" min={0} step={25} value={limit} onChange={(e) => setOption({ auto_approve_limit: Number(e.target.value) })} />
        </label>
        <div className="ml-auto flex items-center gap-2">
          <button className={`btn ${tab === "agent" ? "btn-primary" : ""}`} onClick={() => setTab("agent")}>Agent channel (Muse)</button>
          <button className={`btn ${tab === "human" ? "btn-primary" : ""}`} onClick={() => setTab("human")}>Human channel</button>
          <button className={`btn ${tab === "merchant" ? "btn-primary" : ""}`} onClick={() => setTab("merchant")}>Merchant chat</button>
        </div>
      </div>
      <p className="muted px-1 text-xs">After payment, Umpyr handles every question, change, return, dispute and chargeback on the Muse order. The mandate and the Check decision from <Link className="underline" to="/bjs/check">Part 1</Link> are the evidence that decides fault here. Shipping and logistics stay with BJ's.</p>

      <MetricsStrip workspace="bjs" />

      {tab === "agent" && <AgentChannel caseId={caseId} onCase={setCase} mode={mode} limit={limit} adapter={adapter} />}

      {tab === "human" && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 px-1 text-xs"><Pill text="Human channel" tone="amber" /><span className="muted">Forwarded email from a member; same rules, same audit. Write as a member (orders BJ-7001 to BJ-7013) or pick an example.</span><button className="btn ml-auto !px-2 !py-0.5 text-xs" onClick={start}>New case</button></div>
          <div className="grid grid-cols-[minmax(0,1fr)] gap-3 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
            <section className="panel flex min-h-[70vh] min-w-0 flex-col">
              <div className="flex-1 space-y-3 overflow-y-auto p-3">
                {messages.length === 0 && (
                  <div className="muted text-sm">
                    <p>Start from an example email, or write your own:</p>
                    <ul className="mt-2 space-y-1">
                      {examples.map((ex) => {
                        const text = ex.thread?.[0]?.text ?? ex.subject;
                        return (
                          <li key={ex.ticket_id}>
                            <button className="btn w-full justify-start overflow-hidden text-left" onClick={() => send(text, ex)}>
                              <span className="badge">{ex.category_key}</span>
                              <span className="min-w-0 truncate">{`${text.slice(0, 110)}…`}</span>
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                )}
                {messages.map((m, i) => (
                  <div key={i} className={`max-w-[92%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${m.role === "customer" ? "ml-auto" : ""}`} style={{ background: m.role === "customer" ? "var(--accent-soft)" : "var(--bg)", border: "1px solid var(--border)" }}>
                    <div className="muted mb-1 text-xs">{m.role === "customer" ? "Member" : "Umpyr"}{m.ops ? ` · ${m.ops.status.replace("_", " ")}` : ""}</div>
                    {m.role === "assistant" && m.customer && m.customer.status_lines.length > 0 && <div className="muted mb-1 text-xs">{m.customer.status_lines.join(" · ")}</div>}
                    {m.text}
                    {m.role === "assistant" && m.run && m.run.terms.status === "awaiting" && m.run.execution.status === "waiting_confirmation" && i === messages.length - 1 && (
                      <div className="mt-2 flex flex-wrap gap-2">
                        <button className="btn text-xs" onClick={() => send("I confirm these terms.")}>Confirm these terms{m.run.ruling.amount ? ` · $${m.run.ruling.amount}` : ""}</button>
                      </div>
                    )}
                    {m.role === "assistant" && m.customer && m.customer.queue?.sla && <div className="muted mt-1 text-xs">{`You will hear back ${m.customer.queue.sla}.`}</div>}
                    {m.role === "assistant" && m.customer && m.customer.citations.length > 0 && (
                      <div className="muted mt-1 text-xs">
                        Sources: {m.customer.citations.map((c, j) => (
                          <span key={j}>{j > 0 ? "; " : ""}{c.url ? <a className="underline" href={c.url} target="_blank" rel="noreferrer">{c.document}</a> : c.document}{c.section ? `, ${c.section}` : ""}</span>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
                <div ref={endRef} />
              </div>
              <form className="flex gap-2 border-t p-3" style={{ borderColor: "var(--border)" }} onSubmit={(e) => { e.preventDefault(); send(input); }}>
                <input className="input" placeholder="Describe the problem (include your order number, e.g. BJ-7002)…" value={input} onChange={(e) => setInput(e.target.value)} />
                <button className="btn btn-primary" type="submit">Send</button>
              </form>
            </section>
            <OpsView
              ops={lastOps}
              planner="procedure library, deterministic"
              rulingExtra={lastRun ? <RulingExtra run={lastRun} state={state} onFacts={(overrides) => rerunWith({ overrides })} onStaff={() => rerunWith({ staff_approved: true })} onConfirm={() => rerunWith({ customer_confirmed: true })} /> : null}
              governanceExtra={lastRun ? <GovernanceExtra run={lastRun} /> : null}
            />
          </div>
        </div>
      )}

      {tab === "merchant" && (
        <section className="panel flex min-h-[70vh] min-w-0 flex-col">
          <div className="flex-1 space-y-3 overflow-y-auto p-3 text-sm">
            <p className="muted">Ask about BJ's own data: "what drifts most?", "top categories by pain", "which rule fires most?", "how many cases come from agents?", "which clauses are illustrative?"</p>
            {merchantLog.map((x, i) => (
              <div key={i} className="space-y-1">
                <div className="ml-auto max-w-[85%] rounded-lg px-3 py-2" style={{ background: "var(--accent-soft)" }}>{x.q}</div>
                <div className="max-w-[85%] rounded-lg px-3 py-2 whitespace-pre-wrap" style={{ background: "var(--bg)", border: "1px solid var(--border)" }}>{x.a}</div>
              </div>
            ))}
          </div>
          <form className="flex gap-2 border-t p-3" style={{ borderColor: "var(--border)" }} onSubmit={(e) => { e.preventDefault(); ask(); }}>
            <input className="input" placeholder="Ask about categories, drift, rules…" value={merchantQ} onChange={(e) => setMerchantQ(e.target.value)} />
            <button className="btn btn-primary" type="submit">Ask</button>
          </form>
        </section>
      )}
    </div>
  );
}
