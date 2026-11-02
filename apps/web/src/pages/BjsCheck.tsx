import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import scenariosJson from "../../../../samples/bjs/check_scenarios.json";
import agentCasesJson from "../../../../samples/bjs/agent_cases.json";
import { Disclaimer, Field, Hash, JsonBlock, Pill, StatusDot } from "../components/bjs/ui";
import { LocalCheckAdapter, type CheckAdapter } from "../lib/bjs/adapters";
import { CHECK_POLICY_VERSION } from "../lib/bjs/checkEngine";
import type { CheckScenario } from "../lib/bjs/checkLink";
import type { AgentCasesFile } from "../lib/bjs/negotiation";
import { ADAPTERS, adapterById, adapterTrace, type AdapterId } from "../lib/bjs/protocol";
import { sha256Hex } from "../lib/bjs/sha256";
import type { Binding, CartLine, CheckDecision, CheckRequest, LineDiff, ModelFire } from "../lib/bjs/types";

const SCENARIOS = (scenariosJson as { scenarios: CheckScenario[] }).scenarios;
const AGENT_CASES = (agentCasesJson as unknown as AgentCasesFile).cases;

// Adapter boundary: swap for RemoteCheckAdapter when the engine exposes POST /check/decisions.
const adapter: CheckAdapter = new LocalCheckAdapter();

const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));
const money = (x: number) => x.toFixed(2);

function recalc(req: CheckRequest): CheckRequest {
  for (const l of req.cart.lines) l.line_total = money((Number(l.unit_price) || 0) * (Number(l.quantity) || 0));
  const sub = req.cart.lines.reduce((s, l) => s + Number(l.line_total), 0);
  req.cart.total = money(sub + Number(req.cart.fees) + Number(req.cart.tax));
  return req;
}

const DECISION_TONE: Record<string, "accent" | "amber" | "red" | "muted"> = { approve: "accent", step_up: "amber", hold: "amber", decline: "red", review: "amber" };
const DECISION_TEXT: Record<string, string> = { approve: "Approve", step_up: "Step-up: confirm with the member through their agent", hold: "Hold for review", decline: "Decline" };

function Scores({ d }: { d: CheckDecision }) {
  const rows: [string, number, number][] = [["payment fraud", d.scores.payment_fraud, 70], ["account & abuse", d.scores.account_abuse, 70], ["agent risk", d.scores.agent_risk, 60]];
  return (
    <div className="space-y-1 text-xs">
      {rows.map(([k, v, hi]) => (
        <div key={k} className="flex items-center gap-2">
          <span className="muted w-28">{k}</span>
          <div className="h-2 flex-1 rounded" style={{ background: "var(--border)" }}><div className="h-2 rounded" style={{ width: `${v}%`, background: v >= hi ? "#b91c1c" : v >= 45 ? "var(--warn)" : "var(--accent)" }} /></div>
          <span className="w-8 text-right font-mono">{v}</span>
        </div>
      ))}
    </div>
  );
}

function Diffs({ diffs }: { diffs: LineDiff[] }) {
  if (!diffs.length) return <div className="muted text-xs">Every mandated line is in the cart at the mandated quantity, unit, brand and price; total within the ceiling; fulfilment matches.</div>;
  return (
    <table className="w-full text-[11px]">
      <thead className="muted text-left"><tr><th className="pr-2">Dimension</th><th className="pr-2">Mandate</th><th className="pr-2">Cart</th><th className="pr-2">Severity</th><th>Note</th></tr></thead>
      <tbody>{diffs.map((x, i) => (
        <tr key={i} className="border-t align-top" style={{ borderColor: "var(--border)" }}>
          <td className="py-1 pr-2 font-mono">{x.dimension}{x.line ? <div className="muted font-sans">{x.line}</div> : null}</td>
          <td className="py-1 pr-2">{x.mandate}</td><td className="py-1 pr-2">{x.cart}</td>
          <td className="py-1 pr-2"><Pill text={x.severity} tone={x.severity === "material" ? "red" : x.severity === "benign" ? "amber" : "muted"} /></td>
          <td className="py-1 muted">{x.note}</td>
        </tr>))}</tbody>
    </table>
  );
}

function Contribs({ data }: { data: { contributions?: { label: string; points: number }[] } }) {
  return <ul className="text-[11px]">{(data.contributions ?? []).map((c, i) => <li key={i}><span className="font-mono">+{c.points}</span> <span className="muted">{c.label}</span></li>)}</ul>;
}

export function BjsCheck() {
  const [params, setParams] = useSearchParams();
  const wanted = params.get("scenario");
  const initial = Math.max(0, SCENARIOS.findIndex((s) => s.id === wanted));
  const [idx, setIdx] = useState(initial);
  const [req, setReq] = useState<CheckRequest>(() => clone(SCENARIOS[initial].request));
  const [showJson, setShowJson] = useState(false);
  const [adapterId, setAdapterId] = useState<AdapterId>("pap-mcp");
  const scenario = SCENARIOS[idx];
  const ad = adapterById(adapterId);
  const ledger = useMemo(() => ({ seq: idx + 1, prev_hash: sha256Hex(`bjs-check-ledger:${idx}`) }), [idx]);
  const decision = useMemo(() => adapter.decide(req, ledger, ad.name) as CheckDecision, [req, ledger, ad.name]);
  const linkedCase = scenario.resolve_order_id ? AGENT_CASES.find((c) => c.order_id === scenario.resolve_order_id) : undefined;

  useEffect(() => { if (wanted && wanted !== scenario.id) { const i = SCENARIOS.findIndex((s) => s.id === wanted); if (i >= 0) { setIdx(i); setReq(clone(SCENARIOS[i].request)); } } }, [wanted]); // eslint-disable-line react-hooks/exhaustive-deps
  const pick = (i: number) => { setIdx(i); setReq(clone(SCENARIOS[i].request)); setParams((p) => { const n = new URLSearchParams(p); n.set("scenario", SCENARIOS[i].id); return n; }, { replace: true }); };
  const edit = (fn: (r: CheckRequest) => void) => setReq((r) => { const c = clone(r); fn(c); return recalc(c); });
  const editLine = (i: number, patch: Partial<CartLine>) => edit((r) => { r.cart.lines[i] = { ...r.cart.lines[i], ...patch }; });
  const edited = JSON.stringify(req) !== JSON.stringify(scenario.request);
  const material = decision.intent.diffs.filter((d) => d.severity === "material");

  return (
    <div className="flex flex-col gap-3">
      <div className="panel flex flex-wrap items-center gap-3 px-3 py-2 text-sm">
        <span className="font-semibold">BJ's Wholesale Club</span>
        <span className="badge">Muse order lifecycle · Part 1: Check (before payment)</span>
        <Disclaimer product="Check" />
        <span className="badge badge-amber">BJ's is not live on Muse</span>
        <label className="flex items-center gap-1">
          <span className="muted">Protocol adapter</span>
          <select className="input !w-auto" value={adapterId} onChange={(e) => setAdapterId(e.target.value as AdapterId)} title="Swappable: the adapter is the only code that knows the wire protocol">
            {ADAPTERS.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </label>
        <div className="ml-auto flex items-center gap-2">
          {edited && <Pill text="facts edited: decision recomputed live" tone="amber" />}
          <button className="btn" onClick={() => pick(idx)}>Reset scenario</button>
        </div>
      </div>
      <p className="muted px-1 text-xs">Before BJ's takes payment on a Muse order, Umpyr checks the intent mandate against the cart, scores risk (table stakes), and decides: approve, step-up to the member through their agent, or decline. The decision is hash-committed and kept; <Link className="underline" to="/bjs">Part 2</Link> uses it as evidence when the order is disputed. Onboarding to Muse is included.</p>

      <div className="flex flex-wrap gap-2">
        {SCENARIOS.map((s, i) => <button key={s.id} className={`btn text-xs ${i === idx ? "btn-primary" : ""}`} onClick={() => pick(i)}>{i + 1}. {s.title}</button>)}
      </div>
      <div className="muted px-1 text-xs">{scenario.tagline}</div>

      <div className="grid gap-3 xl:grid-cols-[minmax(0,4fr)_minmax(0,5fr)_minmax(0,4fr)]">
        {/* LEFT: incoming agentic order over the adapter */}
        <div className="space-y-3 text-sm">
          <details className="panel px-3 py-2">
            <summary className="flex cursor-pointer flex-wrap items-center gap-2"><h2 className="font-semibold">Arrives over the adapter</h2><Pill text={ad.short} /><Pill text="swappable" tone="muted" /></summary>
            <ol className="muted mt-1 space-y-0.5 text-[11px]">{adapterTrace(ad, "check").map((s) => <li key={s.step}><b>{s.step}</b> · {s.detail}</li>)}</ol>
            <div className="muted mt-1 text-[11px]">{ad.note} The normalised record is Umpyr's internal ontology, not a wire protocol.</div>
          </details>

          <section className="panel p-3">
            <div className="flex flex-wrap items-center gap-2"><h2 className="font-semibold">Agent</h2><Pill text={req.agent.platform} /><Pill text={req.agent.protocol} tone="muted" /><Pill text={req.agent.attested ? `attested · ${req.agent.attestation_issuer}` : "unattested"} tone={req.agent.attested ? "accent" : "red"} /></div>
            <div className="muted mt-1 font-mono text-[11px] break-all">agent {req.agent.agent_id} · fingerprint {req.agent.fingerprint_id}</div>
            <div className="muted mt-1 text-xs">{req.agent.history.orders_90d} orders / 90 d at {req.agent.history.merchants_90d} merchants · avg basket ${money(req.agent.history.avg_basket_usd)} · dispute rate {(req.agent.history.dispute_rate * 100).toFixed(1)}% · first seen {req.agent.history.first_seen_days_ago} d ago · usual: {req.agent.history.typical_departments.join(", ") || "none"}</div>
            <div className="mt-2 grid grid-cols-2 gap-2">
              <Field label="Platform attestation"><select className="input" value={req.agent.attested ? "yes" : "no"} onChange={(e) => edit((r) => { r.agent.attested = e.target.value === "yes"; })}><option value="yes">present and valid</option><option value="no">missing</option></select></Field>
              <Field label="Mandate signature"><select className="input" value={req.mandate.signature.valid ? "valid" : "invalid"} onChange={(e) => edit((r) => { r.mandate.signature.valid = e.target.value === "valid"; })}><option value="valid">verifies</option><option value="invalid">fails</option></select></Field>
            </div>
          </section>

          <section className="panel p-3">
            <div className="flex flex-wrap items-center gap-2"><h2 className="font-semibold">Intent mandate</h2><Pill text={`${req.mandate.kind} · ${req.mandate.binding}`} tone="muted" /><Pill text={req.mandate.confirmed ? "signed by the member" : "not confirmed"} tone={req.mandate.confirmed ? "accent" : "amber"} /></div>
            {req.mandate.natural_language && <div className="mt-1 text-xs italic">“{req.mandate.natural_language}”</div>}
            <div className="muted mt-1 font-mono text-[11px] break-all">{req.mandate.mandate_id} · {req.mandate.signature.alg} kid {req.mandate.signature.kid} · issuer {req.mandate.signature.issuer} · expires {req.mandate.expires_at}</div>
            <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-2">
              <Field label="Binding"><select className="input" value={req.mandate.binding} onChange={(e) => edit((r) => { r.mandate.binding = e.target.value as Binding; })}><option value="specified">specified</option><option value="delegated">delegated</option><option value="unspecified">unspecified</option></select></Field>
              <Field label="Confirmed by the member"><select className="input" value={req.mandate.confirmed ? "yes" : "no"} onChange={(e) => edit((r) => { r.mandate.confirmed = e.target.value === "yes"; })}><option value="yes">yes</option><option value="no">no</option></select></Field>
              <Field label="Price ceiling ($)"><input className="input" type="number" step={5} value={req.mandate.price_ceiling} onChange={(e) => edit((r) => { r.mandate.price_ceiling = e.target.value; })} onBlur={(e) => edit((r) => { r.mandate.price_ceiling = money(Number(e.target.value) || 0); })} /></Field>
              <Field label="Substitutions"><select className="input" value={req.mandate.substitutions_allowed ? "yes" : "no"} onChange={(e) => edit((r) => { r.mandate.substitutions_allowed = e.target.value === "yes"; })}><option value="yes">allowed</option><option value="no">not allowed</option></select></Field>
            </div>
            <div className="muted mt-2 text-xs">fulfilment {req.mandate.fulfilment.replace(/_/g, " ")}{req.mandate.delivery_window ? ` · window ${req.mandate.delivery_window.start.slice(11, 16)}–${req.mandate.delivery_window.end.slice(11, 16)} UTC` : ""}</div>
            {req.mandate.items.length > 0 ? (
              <table className="mt-2 w-full text-[11px]">
                <thead className="muted text-left"><tr><th>Item</th><th className="w-14">Qty</th><th className="w-14">Unit</th><th className="w-20">Max $/unit</th><th>Sub?</th></tr></thead>
                <tbody>{req.mandate.items.map((it, i) => (
                  <tr key={i} className="border-t" style={{ borderColor: "var(--border)" }}>
                    <td className="py-1 pr-1">{it.description}{it.brand ? <span className="muted"> · {it.brand}</span> : null}</td>
                    <td><input className="input !px-1 !py-0.5" type="number" min={0} value={it.quantity} onChange={(e) => edit((r) => { r.mandate.items[i].quantity = Number(e.target.value); })} /></td>
                    <td>{it.unit}</td>
                    <td><input className="input !px-1 !py-0.5" type="number" step={0.5} value={it.max_unit_price ?? ""} onChange={(e) => edit((r) => { r.mandate.items[i].max_unit_price = e.target.value === "" ? null : e.target.value; })} onBlur={(e) => edit((r) => { r.mandate.items[i].max_unit_price = e.target.value === "" ? null : money(Number(e.target.value) || 0); })} /></td>
                    <td>{it.substitutable ? "yes" : "no"}</td>
                  </tr>))}</tbody>
              </table>
            ) : <div className="muted mt-2 text-xs">No line items: delegated intent, bounded by the ceiling, fulfilment and window.</div>}
          </section>

          <section className="panel p-3">
            <div className="flex flex-wrap items-center gap-2"><h2 className="font-semibold">Cart the agent built</h2><Pill text={`$${req.cart.total}`} tone={Number(req.cart.total) > Number(req.mandate.price_ceiling) ? "red" : "muted"} /><Pill text={req.cart.fulfilment.replace(/_/g, " ")} tone={req.cart.fulfilment === req.mandate.fulfilment ? "muted" : "red"} /></div>
            <table className="mt-2 w-full text-[11px]">
              <thead className="muted text-left"><tr><th>Line</th><th className="w-14">Qty</th><th className="w-16">Unit</th><th className="w-14">Pack</th><th className="w-20">$/unit</th><th className="text-right">Total</th></tr></thead>
              <tbody>{req.cart.lines.map((l, i) => (
                <tr key={i} className="border-t" style={{ borderColor: "var(--border)" }}>
                  <td className="py-1 pr-1">{l.description}{l.substituted_for ? <div className="muted">substituted for {l.substituted_for}</div> : null}<div className="muted">{l.sku} · {l.department}</div></td>
                  <td><input className="input !px-1 !py-0.5" type="number" min={0} value={l.quantity} onChange={(e) => editLine(i, { quantity: Number(e.target.value) })} /></td>
                  <td><select className="input !px-1 !py-0.5" value={l.unit} onChange={(e) => editLine(i, { unit: e.target.value as CartLine["unit"] })}><option>each</option><option>pack</option><option>case</option><option>gallon</option></select></td>
                  <td><input className="input !px-1 !py-0.5" type="number" min={0} value={l.pack_size ?? ""} onChange={(e) => editLine(i, { pack_size: e.target.value === "" ? null : Number(e.target.value) })} /></td>
                  <td><input className="input !px-1 !py-0.5" type="number" step={0.5} value={l.unit_price} onChange={(e) => editLine(i, { unit_price: e.target.value })} onBlur={(e) => editLine(i, { unit_price: money(Number(e.target.value) || 0) })} /></td>
                  <td className="text-right font-mono">{l.line_total}</td>
                </tr>))}</tbody>
            </table>
            <div className="muted mt-1 text-right text-[11px]">fees {req.cart.fees} · tax {req.cart.tax} · <b>total {req.cart.total}</b> · ceiling {req.mandate.price_ceiling}</div>
          </section>

          <section className="panel p-3">
            <div className="flex flex-wrap items-center gap-2"><h2 className="font-semibold">Member (masked)</h2><Pill text={req.customer.tier === "club_plus" ? "Club+" : "Club"} tone="muted" /><Pill text={`membership ${req.customer.membership.status ?? "active"}`} tone={(req.customer.membership.status ?? "active") === "active" ? "accent" : "red"} /><Pill text="tokens and hashes only" /></div>
            <div className="muted mt-1 font-mono text-[11px] break-all">{req.customer.member_token} · email {req.customer.email_hash} · device {req.customer.device_hash} · card bin {req.customer.card.bin_hash} last4 {req.customer.card.last4_hash}</div>
            <div className="muted mt-1 text-xs">tenure {req.customer.tenure_days} d · card on file {req.customer.card.age_days_on_file} d · {req.customer.card.funding}, issuer {req.customer.card.issuer_country} · orders 24h/30d {req.customer.velocity.orders_24h}/{req.customer.velocity.orders_30d} · cards 7d {req.customer.velocity.cards_7d} · ship {req.customer.shipping_region}, IP {req.customer.ip_region}{req.customer.ip_is_datacenter ? " (datacenter)" : ""} · device age {req.customer.device_age_days} d · avg basket ${money(req.customer.history.avg_basket_usd)} · usual: {req.customer.history.typical_departments.join(", ")}</div>
            <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-2">
              <Field label="Membership standing" hint="BJ's sells to members (membership terms); the hard stop before capture is the demo's reading, labelled illustrative"><select className="input" value={req.customer.membership.status ?? "active"} onChange={(e) => edit((r) => { r.customer.membership.status = e.target.value as "active" | "expired" | "none"; })}><option value="active">active</option><option value="expired">expired</option><option value="none">none behind the token</option></select></Field>
              <Field label="Agent fingerprints on membership (30 d)"><input className="input" type="number" min={1} value={req.customer.membership.fingerprints_30d} onChange={(e) => edit((r) => { r.customer.membership.fingerprints_30d = Number(e.target.value); })} /></Field>
              <Field label="Same SKU, other memberships, same agent (24 h)"><input className="input" type="number" min={0} value={req.context.peer_orders_same_sku_24h} onChange={(e) => edit((r) => { r.context.peer_orders_same_sku_24h = Number(e.target.value); })} /></Field>
              <Field label="SKU demand"><select className="input" value={req.context.sku_demand_tier} onChange={(e) => edit((r) => { r.context.sku_demand_tier = e.target.value as CheckRequest["context"]["sku_demand_tier"]; })}><option value="normal">normal</option><option value="high">high</option><option value="constrained">constrained</option></select></Field>
              <Field label="IP from datacenter"><select className="input" value={req.customer.ip_is_datacenter ? "yes" : "no"} onChange={(e) => edit((r) => { r.customer.ip_is_datacenter = e.target.value === "yes"; })}><option value="no">no</option><option value="yes">yes</option></select></Field>
              <Field label="Cards used (7 d)"><input className="input" type="number" min={1} value={req.customer.velocity.cards_7d} onChange={(e) => edit((r) => { r.customer.velocity.cards_7d = Number(e.target.value); })} /></Field>
            </div>
            {(req.context.prior_requests?.length ?? 0) > 0 && <div className="muted mt-2 text-xs">Prior request on this mandate and cart: {req.context.prior_requests!.map((p) => `${p.request_id} at ${p.at.slice(11, 19)} UTC`).join(", ")}.</div>}
          </section>
        </div>

        {/* CENTRE: pipeline */}
        <div className="space-y-2 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2 px-1"><h2 className="font-semibold">Check pipeline</h2><span className="muted text-xs">total {decision.latency_ms} ms (simulated timings) · policy {CHECK_POLICY_VERSION}</span></div>
          {decision.steps.map((s, i) => (
            <section key={s.id} className="panel p-3">
              <div className="flex items-start gap-2">
                <StatusDot status={s.status} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2"><span className="muted font-mono text-xs">{i + 1}</span><span className="font-medium">{s.name}</span>{(s.id === "fraud" || s.id === "abuse" || s.id === "agent") && <Pill text="table stakes" tone="muted" />}{s.id === "intent" && <Pill text="the differentiator" />}<span className="muted ml-auto font-mono text-xs">{s.ms} ms</span></div>
                  <div className="mt-1 text-xs">{s.summary}</div>
                  {s.id === "intent" && <div className="mt-2 overflow-x-auto"><Diffs diffs={decision.intent.diffs} /></div>}
                  {(s.id === "fraud" || s.id === "abuse" || s.id === "agent") && <div className="mt-1"><Contribs data={s.data as { contributions: { label: string; points: number }[] }} /></div>}
                  {s.id === "insight" && (
                    <table className="mt-1 w-full text-[11px]"><tbody>{(s.data as { models: ModelFire[] }).models.map((m) => <tr key={m.name} className="border-t" style={{ borderColor: "var(--border)" }}><td className="py-0.5 pr-2 font-mono">{m.name}@{m.version}</td><td className="pr-2"><Pill text={m.fired ? "fired" : "quiet"} tone={m.fired ? "accent" : "muted"} /></td><td className="muted">{m.score !== undefined ? `${m.score} · ` : ""}{m.contribution}</td></tr>)}</tbody></table>
                  )}
                  {s.id === "decision" && <div className="mt-1 flex flex-wrap gap-1">{decision.reason_codes.map((c) => <Pill key={c} text={c} tone={c.startsWith("INTENT_MATCH") || c.includes("WITHIN_BOUNDS") ? "accent" : "muted"} />)}</div>}
                  {s.id === "commit" && <div className="mt-1 space-y-0.5"><Hash hex={decision.commitment.hash} label="decision sha256" /><Hash hex={decision.ledger.hash} label={`ledger #${decision.ledger.seq}`} /><Hash hex={decision.ledger.prev_hash} label="prev" /></div>}
                  {(s.id === "mandate" || s.id === "intake") && <div className="muted mt-1 font-mono text-[11px]">{(s.data as { model: { name: string; version: string } }).model.name}@{(s.data as { model: { version: string } }).model.version}{s.id === "mandate" ? " · deterministic, no model" : ` · adapter ${ad.id}`}</div>}
                </div>
              </div>
            </section>
          ))}
        </div>

        {/* RIGHT: decision card */}
        <div className="space-y-3 text-sm">
          <section className="panel p-3" style={{ borderColor: decision.decision === "approve" ? "var(--accent)" : decision.decision === "decline" ? "#b91c1c" : "var(--warn)" }}>
            <div className="flex flex-wrap items-center gap-2"><h2 className="font-semibold">Decision to BJ's</h2><Pill text={DECISION_TEXT[decision.decision]} tone={DECISION_TONE[decision.decision]} /></div>
            <div className="mt-1 text-xs">{decision.comparison.umpyr.because}</div>
            <div className="mt-2 flex flex-wrap gap-1"><Pill text={`intent verdict: ${decision.intent.outcome.replace("_", " ")}`} tone={decision.intent.outcome === "match" ? "accent" : decision.intent.outcome === "benign_drift" ? "muted" : "amber"} /><Pill text={`mandate ${decision.mandate.verified ? "verified" : "unverified"}`} tone={decision.mandate.verified ? "accent" : "red"} />{decision.duplicate_of && <Pill text={`duplicate of ${decision.duplicate_of}`} tone="red" />}</div>
            <div className="muted mt-2 text-xs">Risk score (table stakes)</div>
            <div className="mt-1"><Scores d={decision} /></div>
          </section>

          <section className="panel p-3">
            <div className="flex flex-wrap items-center gap-2"><h2 className="font-semibold">Decision record, stored for Resolve</h2><Pill text={decision.decision_id} tone="muted" /></div>
            <div className="muted mt-1 text-xs">Hash-committed before payment and kept with the order. If this order is ever questioned or disputed, Part 2 reads it instead of asking who said what.</div>
            <div className="mt-1"><Hash hex={decision.commitment.hash} label="sha256" /></div>
            <ul className="mt-2 list-disc pl-4 text-xs">
              <li>decision <b>{decision.decision.replace("_", "-")}</b>, intent verdict <b>{decision.intent.outcome.replace("_", " ")}</b>{material.length ? ` (${material.map((d) => d.dimension).join(", ")})` : ""}</li>
              <li>mandate {decision.commitment.committed.mandate_id as string} ({decision.mandate.kid}), agent fingerprint {decision.commitment.committed.agent_fingerprint as string}, cart total ${req.cart.total}</li>
              <li>{decision.decision === "step_up" ? "who answers the step-up (the member with a signed checkout mandate, or the platform) is appended when the agent replies; that one field decides fault later" : "no step-up: the member-signed mandate is the member's confirmation"}</li>
              <li>policy {decision.policy_pack_version}, models {(decision.commitment.committed.models as string[]).length}</li>
            </ul>
            {scenario.resolve_order_id ? (
              <div className="mt-2 text-xs">This record is order <b>{scenario.resolve_order_id}</b> in Part 2{linkedCase ? <>: <Link className="underline" to={`/bjs?tab=agent&case=${linkedCase.case_id}`}>{linkedCase.title} →</Link></> : "."}</div>
            ) : <div className="muted mt-2 text-xs">No order follows this decision in the demo{decision.decision === "decline" ? "; the remedy is in the reply to the agent" : ""}.</div>}
          </section>

          <section className="panel p-3">
            <h2 className="font-semibold">Risk score alone vs the lifecycle view</h2>
            <div className="mt-2 grid gap-2 sm:grid-cols-2">
              <div className="rounded-md p-2" style={{ background: "var(--bg)", border: "1px solid var(--border)" }}>
                <div className="flex items-center gap-2 text-xs"><span className="muted">Card + device + velocity</span><Pill text={decision.comparison.ordinary_fraud_tool.decision} tone={DECISION_TONE[decision.comparison.ordinary_fraud_tool.decision]} /></div>
                <div className="mt-1 text-xs">{decision.comparison.ordinary_fraud_tool.because}</div>
              </div>
              <div className="rounded-md p-2" style={{ background: "var(--accent-soft)", border: "1px solid var(--border)" }}>
                <div className="flex items-center gap-2 text-xs"><span className="muted">+ mandate + agent + intent + membership</span><Pill text={decision.decision.replace("_", "-")} tone={DECISION_TONE[decision.decision]} /></div>
                <div className="mt-1 text-xs">{decision.comparison.umpyr.because}</div>
              </div>
            </div>
            <div className="muted mt-2 text-xs">Risk scoring is table stakes. The difference is one system across the lifecycle: what the member asked for is captured here and decides fault later.</div>
          </section>

          <section className="panel p-3">
            <div className="flex flex-wrap items-center gap-2"><h2 className="font-semibold">Reply to the agent</h2><Pill text={String(decision.actions[0].message_to_agent.type)} tone="muted" /><Pill text={`over ${ad.short}`} tone="muted" /></div>
            <JsonBlock value={decision.actions[0].message_to_agent} maxHeight={300} />
          </section>

          <section className="panel p-3">
            <div className="flex flex-wrap items-center gap-2"><h2 className="font-semibold">Normalised record (umpyr-check/0.1)</h2><button className="btn ml-auto !px-2 !py-0.5 text-xs" onClick={() => setShowJson(!showJson)}>{showJson ? "hide" : "show full"}</button></div>
            <JsonBlock value={showJson ? { ...decision, steps: undefined } : { protocol: decision.protocol, decision_id: decision.decision_id, decision: decision.decision, reason_codes: decision.reason_codes, intent: { outcome: decision.intent.outcome, binding: decision.intent.binding, confirmed: decision.intent.confirmed }, scores: decision.scores, mandate: decision.mandate, latency_ms: decision.latency_ms, commitment: { algorithm: decision.commitment.algorithm, canonicalization: decision.commitment.canonicalization, hash: decision.commitment.hash }, ledger: decision.ledger, policy_pack_version: decision.policy_pack_version }} maxHeight={showJson ? 520 : 300} />
          </section>
        </div>
      </div>
    </div>
  );
}
