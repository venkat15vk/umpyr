import { useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ANON, b, BJS_BASE, merchantName } from "../lib/brand";
import { agentCases as cases, categories, history, orders, pack, procedures, scenarios } from "../lib/bjs/fixtures";
import { EvidenceChips, RulingPanel, StatePanel, Transcript } from "../components/bjs/Negotiation";
import { Hash, JsonBlock, Pill, RaciChips, RaciLegend } from "../components/bjs/ui";
import { negotiate, type NegotiationCtx } from "../lib/bjs/negotiation";
import { ADAPTERS, adapterById, adapterTrace, type AdapterId } from "../lib/bjs/protocol";
import type { Raci } from "../lib/bjs/types";

const nctx: NegotiationCtx = { pack, procedures, orders, categories, scenarios, history };
const CASE = cases.find((c) => c.case_id === b("BJ-A-3")) ?? cases[0]; // the paper-plates order: Check step-up, platform auto-confirm, dispute, negotiation, staff approval, execution

type Step = { key: string; title: string; who: string; raci: Raci; outside?: boolean; highlight?: boolean; body: ReactNode };

function StepRow({ s, i }: { s: Step; i: number }) {
  return (
    <li className="relative pl-8">
      <span className="absolute left-0 top-1 flex h-6 w-6 items-center justify-center rounded-full border font-mono text-[11px]" style={{ borderColor: s.outside ? "var(--muted)" : s.highlight ? "var(--accent)" : "var(--border)", background: s.highlight ? "var(--accent-soft)" : "var(--panel)" }}>{i + 1}</span>
      <section className={`panel p-3 text-sm ${s.outside ? "border-dashed" : ""}`} style={s.highlight ? { borderColor: "var(--accent)", boxShadow: "0 0 0 2px var(--accent-soft)" } : s.outside ? { borderStyle: "dashed", background: "var(--bg)" } : undefined}>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-semibold">{s.title}</h2>
          {s.outside && <Pill text="outside Umpyr" tone="muted" />}
          {s.highlight && <Pill text="the key moment" />}
          <span className="muted text-xs">{s.who}</span>
        </div>
        <div className="mt-1"><RaciChips raci={s.raci} /></div>
        <div className="mt-2 text-xs">{s.body}</div>
      </section>
    </li>
  );
}

export function BjsLifecycle() {
  const [adapter, setAdapter] = useState<AdapterId>("pap-mcp");
  const [limit, setLimit] = useState(100);
  const ad = adapterById(adapter);
  const n = useMemo(() => negotiate(CASE, nctx, { auto_approve_limit: limit, mode: "autonomous", staff_approved: true, confirmation_by: null, adapter }), [adapter, limit]);
  const order = n.order;
  const check = n.check!;
  const d = check.decision;
  const m = check.request.mandate;
  const stepUp = d.actions[0].message_to_agent as { present_to_customer?: { line: string; authorised: string; in_cart: string }[] };
  const staffTurn = n.turns.find((t) => t.kind === "staff_approval");
  const executeTurn = n.turns.find((t) => t.kind === "execute");
  const capture = order.charges[0];

  const steps: Step[] = [
    { key: "ask", title: "The member asks Muse", who: "member → personal agent", raci: { customer: "A", customer_agent: "R" }, body: <><div className="italic">“{m.natural_language}”</div><div className="muted mt-1">Muse turns the ask into a signed {m.kind} mandate {m.mandate_id}: {m.items.map((i) => `${i.quantity} ${i.unit} ${i.description}${i.max_unit_price ? ` ≤ $${i.max_unit_price}` : ""}`).join("; ")}; ceiling ${m.price_ceiling}; substitutions {m.substitutions_allowed ? "allowed" : "off"}; {m.fulfilment.replace(/_/g, " ")}. Member {order.member.member_token} (masked).</div></> },
    { key: "adapter", title: b("Muse connects to BJ's through the protocol adapter"), who: `${ad.name} · swappable`, raci: { customer_agent: "R", agent_platform: "C", umpyr: "A", merchant: "I" }, body: <><div>{ad.note} Umpyr defines no protocol; onboarding {merchantName("bjs")} to Muse is included.</div><details className="muted mt-1"><summary className="cursor-pointer">what the adapter does</summary><ol className="space-y-0.5">{adapterTrace(ad, "check").map((s) => <li key={s.step}><b>{s.step}</b> · {s.detail}</li>)}</ol></details></> },
    { key: "check", title: "Umpyr Check, before payment", who: b("Umpyr for BJ's · milliseconds"), raci: { umpyr: "R", merchant: "A", customer_agent: "I" }, body: <>
        <div className="flex flex-wrap items-center gap-2"><Pill text={`mandate ${d.mandate.verified ? "verified" : "unverified"}`} tone={d.mandate.verified ? "accent" : "red"} /><Pill text={`risk: fraud ${d.scores.payment_fraud} · account ${d.scores.account_abuse} · agent ${d.scores.agent_risk}`} tone="muted" /><Pill text="table stakes" tone="muted" /><Pill text={`intent verdict: ${d.intent.outcome.replace("_", " ")}`} tone="amber" /><Pill text={`decision: ${d.decision.replace("_", "-")}`} tone="amber" /></div>
        <table className="mt-2 w-full text-[11px]"><thead className="muted text-left"><tr><th className="pr-2">Line</th><th className="pr-2">Mandate</th><th>Cart</th></tr></thead><tbody>{d.intent.diffs.filter((x) => x.severity === "material").map((x, i) => <tr key={i} className="border-t align-top" style={{ borderColor: "var(--border)" }}><td className="py-0.5 pr-2 font-mono">{x.dimension}</td><td className="py-0.5 pr-2">{x.mandate}</td><td className="py-0.5">{x.cart}</td></tr>)}</tbody></table>
        <div className="mt-2">{d.comparison.umpyr.because}</div>
        <div className="mt-1"><Hash hex={d.commitment.hash} label={`decision record ${d.decision_id} · sha256`} /></div>
        <div className="muted mt-1">Stored with the order. Step-up sent to Muse: {stepUp.present_to_customer?.map((l) => `${l.line}: authorised ${l.authorised}, in cart ${l.in_cart}`).join("; ")}.</div>
      </> },
    { key: "stepup", title: "The step-up is answered", who: "agent platform, not the member", raci: { customer_agent: "R", agent_platform: "R", customer: "I", umpyr: "C" }, body: <><div>{check.link.note}</div><div className="muted mt-1 font-mono text-[11px]">confirmation_by = {check.link.confirmation_by} · member_signature = {String(check.link.confirmation?.member_signature)} · this field decides fault in step 10</div></> },
    { key: "pay", title: b("Payment captured by BJ's processor"), who: b("BJ's and its processor, not Umpyr"), raci: { merchant: "A", third_party: "R", umpyr: "I", customer_agent: "I" }, body: <div>Capture {capture.charge_id}: ${capture.amount} on card {capture.card_token}, {capture.at}. Order {order.order_id} created. Money never touches Umpyr.</div> },
    { key: "ship", title: "Picking and delivery", who: `${order.fulfilment.partner ?? "club"} · ${order.fulfilment.method.replace(/_/g, " ")}`, raci: { merchant: "A", third_party: "R", customer: "I" }, outside: true, body: <div>Shipping and logistics are out of Umpyr's scope. The order was picked by a {order.fulfilment.picked_by?.replace(/_/g, " ")} and delivered at {order.fulfilment.delivered_at?.slice(11, 16)} UTC: {order.items.map((i) => `${i.quantity} ${i.unit} ${i.name}`).join("; ")}.</div> },
    { key: "question", title: "Post-order question from Muse", who: "personal agent → Umpyr Resolve", raci: { customer_agent: "R", umpyr: "R", merchant: "A", customer: "I" }, body: <><div className="rounded-md px-2 py-1" style={{ background: "var(--panel)", border: "1px solid var(--border)" }}><b>Muse:</b> Can order {order.order_id} be changed to 10 packs before it ships?</div><div className="mt-1 rounded-md px-2 py-1" style={{ background: "var(--accent-soft)", border: "1px solid var(--border)" }}><b>{b("Umpyr (for BJ's):")}</b> It was delivered at {order.fulfilment.delivered_at?.slice(11, 16)} UTC, so it can no longer be changed. If the quantity is not what the member asked for, open a claim and I will look at it against the mandate and the Check record.</div></> },
    { key: "dispute", title: "Dispute: Muse self-reports the error", who: "personal agent → Umpyr Resolve, over the adapter", raci: { customer_agent: "R", customer: "A", umpyr: "I" }, body: <><div>{n.turns[0].text}</div><EvidenceChips evidence={n.turns[0].evidence} /></> },
    { key: "pull", title: "Resolve pulls the stored Check evidence", who: "Umpyr", raci: { umpyr: "R", merchant: "A" }, body: <><div>The same record from step 3, by id. No re-asking who said what.</div><EvidenceChips evidence={n.turns[1].evidence.filter((e) => e.kind === "check_record" || e.kind === "mandate")} /></> },
    { key: "negotiate", title: "Agent-to-agent negotiation", who: b("Umpyr's agent (for BJ's, pack " + nctx.pack.version + ") ↔ Muse"), raci: { umpyr: "R", customer_agent: "R", merchant: "A", customer: "I" }, highlight: true, body: <><Transcript turns={n.turns.filter((t) => t.kind !== "execute" && t.kind !== "audit")} shown={n.turns.length} compact /><details className="mt-3"><summary className="muted cursor-pointer">negotiation state at the end</summary><div className="mt-2"><StatePanel state={n.final} shown={n.turns.length} total={n.turns.length} /></div></details></> },
    { key: "approve", title: b("A BJ's approver signs off"), who: b("BJ's staff · amount above the auto-approve ceiling"), raci: staffTurn?.raci ?? { merchant_staff: "A", umpyr: "R" }, body: <div>{staffTurn?.text ?? "Not needed at this ceiling."} <span className="muted">Raise the ceiling above $970.92 in the header and this step disappears.</span></div> },
    { key: "execute", title: b("Resolution executed by BJ's"), who: b("BJ's order service (mock API)"), raci: executeTurn?.raci ?? { merchant: "R", umpyr: "C" }, body: <><div>{executeTurn?.text}</div>{n.execution.request && <div className="mt-2 grid gap-2 md:grid-cols-2"><JsonBlock value={n.execution.request} maxHeight={180} /><JsonBlock value={n.execution.response} maxHeight={180} /></div>}</> },
    { key: "audit", title: "Signed audit record", who: "Umpyr", raci: { umpyr: "R", merchant: "I", customer_agent: "I" }, body: <><Hash hex={n.audit.hash} label="audit sha256" /><Hash hex={n.audit.signature} label={`signature hmac-sha256 kid=${n.audit.kid}`} /><details className="mt-1"><summary className="muted cursor-pointer">record</summary><JsonBlock value={n.audit.record} maxHeight={240} /></details></> },
    { key: "close", title: "Lifecycle closure", who: b("BJ's, with a signal back to Check"), raci: { merchant: "R", merchant_staff: "A", umpyr: "C", agent_platform: "I" }, body: <div>{n.lead_run?.lifecycle_note}</div> },
  ];

  return (
    <div className="flex flex-col gap-3">
      <div className="panel flex flex-wrap items-center gap-3 px-3 py-2 text-sm">
        <span className="font-semibold">{b("BJ's Wholesale Club")}</span>
        <span className="badge">End-to-end Muse order · agent-to-agent</span>
        <span className="badge badge-amber">synthetic data</span>
        {ANON ? <span className="badge badge-amber" title="Illustrative demo; the merchant is a stand-in, not a client, and is not live on Muse.">illustrative · stand-in merchant · not live on Muse</span> : <span className="badge badge-amber" title="Illustrative demo, not affiliated with BJ's Wholesale Club. BJ's is not live on Muse.">illustrative · not affiliated · BJ's is not live on Muse</span>}
        <label className="flex items-center gap-1"><span className="muted">Protocol adapter</span><select className="input !w-auto" value={adapter} onChange={(e) => setAdapter(e.target.value as AdapterId)}>{ADAPTERS.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
        <label className="flex items-center gap-1"><span className="muted">Auto-approve ≤ $</span><input className="input !w-24" type="number" min={0} step={25} value={limit} onChange={(e) => setLimit(Number(e.target.value))} /></label>
        <div className="ml-auto flex items-center gap-2 text-xs"><Link className="btn" to={`${BJS_BASE}/check?scenario=pack_size_vs_quantity`}>Part 1: Check</Link><Link className="btn" to={`${BJS_BASE}?tab=agent&case=${CASE.case_id}`}>Part 2: Resolve</Link></div>
      </div>
      <p className="muted px-1 text-xs">One order, one timeline, one system: from the intent check before payment to the signed record after the dispute. Shipping and logistics sit outside, marked. <RaciLegend /></p>
      <ol className="space-y-3">{steps.map((s, i) => <StepRow key={s.key} s={s} i={i} />)}</ol>
      <RulingPanel n={n} />
    </div>
  );
}
