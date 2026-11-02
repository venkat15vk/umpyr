/**
 * Agent-to-agent negotiation for the BJ's Resolve demo.
 *
 * A complaint from the member's personal agent (Meta Muse) arrives over the protocol adapter. Umpyr's agent acts for BJ's
 * under the compiled policy pack: it pulls the Check record and the intent mandate that preceded payment, runs the rules
 * on every open issue, and takes a position per turn (opening, held, conceded, changed on new evidence). Muse's moves
 * come from the case fixture (samples/bjs/agent_cases.json); Umpyr's are computed here, so changing the auto-approve
 * ceiling, the mode, or who answered the Check step-up changes the negotiation. Deterministic; no model calls.
 */
import { precedentFor } from "./chat";
import { checkEvidenceFor, orderWithConfirmation, type CheckEvidence, type CheckScenario } from "./checkLink";
import { adapterById, type AdapterId } from "./protocol";
import { DEMO_KID, DEMO_SIGNING_KEY, friendly, runCase, type ResolveRun, type RunOptions } from "./resolveEngine";
import { canonical, hmacSha256Hex, sha256Hex } from "./sha256";
import type { HistoryFile } from "./chat";
import type { Category, Claim, ConfirmationBy, Decision, Fault, Order, Procedure, Raci, RulePack, Ticket } from "./types";

// ---------- fixture shapes ----------

export type AgentComplaint = {
  adapter: AdapterId;
  wire: { protocol: string; transport: string; tool: string };
  normalized: string;
  type: "claim";
  agent: { platform: string; agent_id: string; attested: boolean; attestation_issuer: string };
  member_token: string;
  order_id: string;
  mandate_id: string;
  claims: { reason: string; item_ids: string[]; detail: string; requested_remedy: string; [k: string]: unknown }[];
  consent: { scope: string; granted_at: string };
  at: string;
};

export type Issue = { key: string; label: string; short?: string; category_key: string; activate_on_counter?: boolean; extracted: Partial<Claim> & { item_id: string } };

export type MuseScript = {
  opening: string;
  facts_on_request?: Record<string, Record<string, unknown>>; // issue key -> facts (+ _text)
  facts_on_counter?: Record<string, Record<string, unknown>>; // issue key -> facts Muse argues with (+ _text)
  member_facts?: Record<string, Record<string, unknown>>; // issue key -> facts only the member can give (+ _refer, _text)
  counter_rounds?: number;
  pushback?: string[];
  texts?: Partial<Record<"accept" | "accept_terms" | "acknowledge" | "wait", string>>;
};

export type AgentCase = { case_id: string; kind: "converge" | "hold_firm" | "ceiling" | "policy_gap"; order_id: string; title: string; tagline: string; complaint: AgentComplaint; issues: Issue[]; muse: MuseScript };
export type AgentCasesFile = { workspace: string; version: string; note: string; cases: AgentCase[] };

// ---------- negotiation shapes ----------

export type Speaker = "muse" | "umpyr" | "bjs" | "bjs_staff" | "member";
export type Position = "opening" | "held" | "conceded" | "changed_on_evidence" | "accepted" | "n/a";
export type EvidenceKind = "mandate" | "check_record" | "order" | "policy_rule" | "history" | "attachment";
export type Evidence = { kind: EvidenceKind; ref: string; detail: string; url?: string | null };
export type OfferLine = { issue: string; label: string; decision: Decision; remedy: string | null; amount: string | null; rule_id: string | null; clause_id: string | null; fault: Fault; illustrative: boolean; note: string | null };
export type Offer = { total: string; lines: OfferLine[] };
export type TurnKind = "claim" | "offer" | "hold" | "deny" | "ask_fact" | "provide_fact" | "counter" | "refer_to_member" | "member_decision" | "accept" | "needs_approval" | "staff_approval" | "terms" | "terms_accepted" | "execute" | "audit" | "escalate" | "pushback" | "acknowledge";
export type Termination = { kind: "agreement" | "ruling_accepted" | "escalate_staff" | "escalate_customer"; reason: string; sla?: string | null };
export type NegotiationState = {
  open_issues: string[];
  muse_asks: string[];
  current_offer: Offer | null;
  policy_limits: { auto_approve_limit: number; mode: string; ceiling_hit: boolean; staff_approved: boolean };
  termination: Termination | null;
  customer_referral: { turn: number; resumed: boolean } | null;
  terms_status: "n/a" | "awaiting" | "confirmed";
  execution_status: "none" | "waiting_approval" | "waiting_confirmation" | "executed" | "shadow";
};
export type Turn = { n: number; speaker: Speaker; kind: TurnKind; text: string; tech: string[]; evidence: Evidence[]; position: Position; offer: Offer | null; raci: Raci; state: NegotiationState; runs?: Record<string, ResolveRun> };

export type Negotiation = {
  case: AgentCase;
  order: Order;
  check: CheckEvidence | null;
  turns: Turn[];
  final: NegotiationState;
  fault: { headline: Fault; by_issue: Record<string, Fault> };
  terms: { terms: Record<string, unknown>; hash: string; status: "n/a" | "awaiting" | "confirmed"; confirmation: Record<string, unknown> | null } | null;
  execution: { status: NegotiationState["execution_status"]; request: Record<string, unknown> | null; response: Record<string, unknown> | null };
  audit: { record: Record<string, unknown>; hash: string; signature: string; kid: string };
  runs: Record<string, ResolveRun>;
  lead_run: ResolveRun | null;
  umpyr_turn_count: number;
};

export type NegotiationCtx = { pack: RulePack; procedures: Procedure[]; orders: Order[]; categories: Category[]; scenarios: CheckScenario[]; history: HistoryFile };
export type NegotiationOptions = { auto_approve_limit: number; mode: "autonomous" | "assist" | "shadow"; staff_approved: boolean; confirmation_by?: ConfirmationBy | null; adapter: AdapterId };

const money = (x: number) => x.toFixed(2);
const FAULT_WHO: Record<Fault, string> = { customer: "the member", agent: "the member's agent", agent_platform: "the agent platform", merchant: "BJ's", third_party: "the fulfilment partner", indeterminate: "not determinable" };
const sum = (lines: OfferLine[]) => money(lines.reduce((s, l) => s + (l.amount ? Number(l.amount) : 0), 0));
const isMoney = (d: Decision) => d === "approve" || d === "partial";

// ---------- helpers ----------

function ticketFor(c: AgentCase, issue: Issue, overrides: Record<string, unknown>): Ticket {
  const claim = c.complaint.claims.find((x) => x.reason === issue.extracted.reason) ?? c.complaint.claims[0];
  return {
    ticket_id: `${c.case_id}-${issue.key}`,
    category_key: issue.category_key,
    channel: "agent",
    created_at: c.complaint.at,
    subject: issue.label,
    order_id: c.order_id,
    agent_message: { protocol: c.complaint.normalized, type: "claim", agent: { platform: c.complaint.agent.platform, agent_id: c.complaint.agent.agent_id, attested: c.complaint.agent.attested }, member_token: c.complaint.member_token, order_id: c.order_id, claim, consent: c.complaint.consent, via: c.complaint.wire },
    extracted: { ...issue.extracted, ...overrides } as Ticket["extracted"],
    is_synthetic: true,
  };
}

function fill(text: string, vars: Record<string, string>): string {
  return text.replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? `{${k}}`);
}

function raciFor(kind: TurnKind, fault: Fault | null): Raci {
  switch (kind) {
    case "claim": case "counter": case "pushback": case "provide_fact": case "accept": case "terms_accepted": case "acknowledge": return { customer_agent: "R", customer: "A", umpyr: "I" };
    case "refer_to_member": return { customer_agent: "R", customer: "A", umpyr: "I" };
    case "member_decision": return { customer: "A", customer_agent: "R", umpyr: "I" };
    case "needs_approval": return { umpyr: "R", merchant: "A", merchant_staff: "C", customer_agent: "I" };
    case "staff_approval": return { merchant_staff: "A", umpyr: "R", merchant: "I", customer_agent: "I" };
    case "execute": return { merchant: "R", umpyr: "C", customer_agent: "I", ...(fault === "third_party" ? { third_party: "I" as const } : {}) };
    case "escalate": return { umpyr: "R", merchant_staff: "A", merchant: "I", customer_agent: "I" };
    default: return { umpyr: "R", merchant: "A", customer_agent: "I", ...(fault === "third_party" ? { third_party: "C" as const } : {}) };
  }
}

function lineOf(issue: Issue, run: ResolveRun, pack: RulePack): OfferLine {
  const clause = pack.clauses.find((c) => c.clause_id === run.ruling.winning_clause_id);
  return { issue: issue.key, label: issue.label, decision: run.ruling.decision, remedy: run.ruling.remedy, amount: isMoney(run.ruling.decision) ? run.ruling.amount : null, rule_id: run.ruling.winning_rule_id, clause_id: run.ruling.winning_clause_id, fault: run.ruling.fault, illustrative: !!clause && !clause.confirmed, note: run.ruling.note };
}

const ruleEvidence = (l: OfferLine, pack: RulePack): Evidence | null => {
  if (!l.rule_id) return null;
  const clause = pack.clauses.find((c) => c.clause_id === l.clause_id);
  return { kind: "policy_rule", ref: `${l.rule_id} · pack ${pack.version}`, detail: `${clause?.title ?? l.clause_id}${clause && !clause.confirmed ? " (illustrative practice rule)" : ""}: ${l.decision}${l.remedy ? ` · ${l.remedy}` : ""}${l.amount ? ` · $${l.amount}` : ""} · fault ${l.fault}`, url: clause?.source_url ?? null };
};

function orderEvidence(order: Order, issue: Issue): Evidence {
  const item = order.items.find((i) => i.item_id === issue.extracted.item_id) ?? order.items[0];
  const cond = item.condition && item.condition !== "ok" ? ` · condition ${item.condition}${item.substituted_with ? ` → ${item.substituted_with}` : ""}` : "";
  return { kind: "order", ref: `${order.order_id} · ${item.item_id}`, detail: `${item.name}: ${item.quantity} ${item.unit} × $${item.price} = $${item.line_total}${cond} · ${order.fulfilment.method.replace(/_/g, " ")}${order.fulfilment.partner ? ` via ${order.fulfilment.partner}` : ""} · placed by ${order.placed_by}${order.fulfilment.substitutions_allowed === false ? " · substitutions off" : ""}` };
}

function checkEvidence(ev: CheckEvidence | null): Evidence[] {
  if (!ev) return [];
  const d = ev.decision, l = ev.link, m = ev.request.mandate;
  const lines = m.items.map((i) => `${i.quantity} ${i.unit} ${i.description}${i.max_unit_price ? ` ≤ $${i.max_unit_price}` : ""}`).join("; ");
  return [
    { kind: "mandate", ref: m.mandate_id, detail: `${m.kind} mandate, ${m.binding}, ${m.confirmed ? "signed by the member" : "unconfirmed"} (${m.signature.alg} kid ${m.signature.kid}): ${lines}${m.items.length ? ";" : ""} ceiling $${m.price_ceiling}; substitutions ${m.substitutions_allowed ? "allowed" : "off"}.${m.natural_language ? ` "${m.natural_language}"` : ""}` },
    { kind: "check_record", ref: d.decision_id, detail: `Umpyr Check ${d.decision.replace("_", "-")} · intent ${d.intent.outcome.replace("_", " ")}${d.intent.diffs.length ? ` (${d.intent.diffs.filter((x) => x.severity === "material").map((x) => x.dimension).join(", ")})` : ""} · step-up answered by ${l.confirmation_by.replace("_", " ")}${l.confirmation?.member_signature ? " with a signed checkout mandate" : l.confirmation_by === "agent_platform" ? " (no member signature)" : ""} · sha256 ${d.commitment.hash.slice(0, 16)}…${ev.what_if ? " · WHAT-IF" : ""}` },
  ];
}

// ---------- the negotiation ----------

export function negotiate(c: AgentCase, ctx: NegotiationCtx, opts: NegotiationOptions): Negotiation {
  const adapter = adapterById(opts.adapter);
  const baseOrder = ctx.orders.find((o) => o.order_id === c.order_id);
  if (!baseOrder) throw new Error(`order ${c.order_id} missing`);
  const order = orderWithConfirmation(baseOrder, opts.confirmation_by);
  const check = checkEvidenceFor(baseOrder, ctx.scenarios, opts.confirmation_by, adapter.name);
  const engineCtx = { pack: ctx.pack, procedures: ctx.procedures, orders: ctx.orders.map((o) => (o.order_id === order.order_id ? order : o)), categories: ctx.categories };
  const vars: Record<string, string> = { mandate: (order.check?.mandate_id ?? "").replace(/^m_/, ""), member: order.member.member_token.replace(/^mem_/, ""), check_id: (check?.decision.decision_id ?? "").replace(/^chk_/, ""), photo_hash: sha256Hex(`photo:${c.case_id}`).slice(0, 16), offer: "0.00", terms_hash: "" };
  const texts = c.muse.texts ?? {};

  const turns: Turn[] = [];
  const overrides: Record<string, Record<string, unknown>> = {};
  const active = new Set(c.issues.filter((i) => !i.activate_on_counter).map((i) => i.key));
  const asks = new Set(c.issues.map((i) => i.key));
  let prevOffer: Offer | null = null;
  let prevDecisions: Record<string, Decision> = {};
  let counterRounds = 0, pushbacks = 0;
  let accepted = false, termsIssued = false, staffApproved = false, customerConfirmed = false;
  let referral: NegotiationState["customer_referral"] = null;
  let termination: Termination | null = null;
  let runs: Record<string, ResolveRun> = {};
  let offer: Offer = { total: "0.00", lines: [] };
  let terms: Negotiation["terms"] = null;
  let execution: Negotiation["execution"] = { status: "none", request: null, response: null };
  let ceilingHit = false;
  const runOpts = (): RunOptions => ({ channel: "agent", mode: opts.mode, auto_approve_limit: opts.auto_approve_limit, facts: {}, customer_confirmed: customerConfirmed, staff_approved: staffApproved });

  const state = (): NegotiationState => ({
    open_issues: termination || accepted ? [] : c.issues.filter((i) => asks.has(i.key)).map((i) => i.label),
    muse_asks: c.issues.filter((i) => asks.has(i.key)).map((i) => i.label),
    current_offer: offer.lines.length ? offer : null,
    policy_limits: { auto_approve_limit: opts.auto_approve_limit, mode: opts.mode, ceiling_hit: ceilingHit, staff_approved: staffApproved },
    termination,
    customer_referral: referral,
    terms_status: terms?.status ?? "n/a",
    execution_status: execution.status,
  });
  const push = (speaker: Speaker, kind: TurnKind, text: string, evidence: Evidence[] = [], position: Position = "n/a", withRuns = false, tech: string[] = []) => {
    const fault = offer.lines[0]?.fault ?? null;
    turns.push({ n: turns.length + 1, speaker, kind, text, tech, evidence, position, offer: offer.lines.length ? { ...offer, lines: [...offer.lines] } : null, raci: raciFor(kind, fault), state: state(), runs: withRuns ? { ...runs } : undefined });
  };
  const short = (key: string) => { const i = c.issues.find((x) => x.key === key); return i?.short ?? i?.label ?? key; };
  const who = (f: Fault) => FAULT_WHO[f];
  const checkTech = (): string[] => check ? [
    `Check ${check.decision.decision_id} · ${check.decision.decision} · intent ${check.decision.intent.outcome} · step-up by ${check.link.confirmation_by}${check.what_if ? " (what-if)" : ""} · sha256 ${check.decision.commitment.hash.slice(0, 16)}…`,
    `mandate ${check.request.mandate.mandate_id} · kid ${check.request.mandate.signature.kid} · ceiling $${check.request.mandate.price_ceiling} · substitutions ${check.request.mandate.substitutions_allowed ? "allowed" : "off"}`,
  ] : ["no Check record on this order"];
  const lineTech = (l: OfferLine) => `${c.issues.find((i) => i.key === l.issue)?.extracted.item_id ?? l.issue} ${l.rule_id ?? "no rule fired"} → ${l.decision}${l.amount ? ` ${l.remedy} $${l.amount}` : ""} · fault ${l.fault}${l.illustrative ? " · illustrative practice rule" : ""}`;
  const packTech = `pack ${ctx.pack.version}`;
  const say = (l: OfferLine) => isMoney(l.decision) ? `$${l.amount} for ${short(l.issue)}` : `nothing for ${short(l.issue)}`;

  // turn 1: the complaint, over the adapter
  push("muse", "claim", fill(c.muse.opening, vars), [{ kind: "attachment", ref: `${adapter.short} · ${c.complaint.wire.tool}`, detail: `${c.complaint.wire.protocol} claim over ${adapter.transport}, normalised to ${c.complaint.normalized}; attested by ${c.complaint.agent.attestation_issuer}; consent scope ${c.complaint.consent.scope}.` }, ...(check ? [{ kind: "mandate" as const, ref: check.request.mandate.mandate_id, detail: `cited by the agent as the member's instruction` }] : [])], "opening", false,
    [`order ${c.order_id} · member ${order.member.member_token} · mandate ${order.check?.mandate_id ?? "none"}`, `${adapter.short} ${c.complaint.wire.tool} → ${c.complaint.normalized} · attested by ${c.complaint.agent.attestation_issuer}`]);

  for (let guard = 0; guard < 40 && !termination; guard++) {
    // ---- Umpyr's turn: run every active issue ----
    runs = {};
    for (const issue of c.issues) if (active.has(issue.key)) runs[issue.key] = runCase(ticketFor(c, issue, overrides[issue.key] ?? {}), engineCtx, runOpts());
    const lines = c.issues.filter((i) => active.has(i.key)).map((i) => lineOf(i, runs[i.key], ctx.pack));
    const needFact = lines.filter((l) => l.decision === "needs_fact");
    const escalated = lines.filter((l) => l.decision === "escalate");
    const approved = lines.filter((l) => isMoney(l.decision));
    const denied = lines.filter((l) => l.decision === "deny");
    const held = c.issues.filter((i) => asks.has(i.key) && !active.has(i.key));
    const newOffer: Offer = { total: sum(approved), lines: [...approved, ...denied] };
    const changedFacts = lines.some((l) => prevDecisions[l.issue] !== undefined && prevDecisions[l.issue] !== l.decision);
    const conceded = approved.some((l) => prevDecisions[l.issue] === undefined && prevOffer !== null);
    const position: Position = prevOffer === null ? "opening" : conceded ? "conceded" : changedFacts || newOffer.total !== prevOffer.total ? "changed_on_evidence" : "held";
    const evidence: Evidence[] = [...checkEvidence(check), ...c.issues.filter((i) => active.has(i.key)).map((i) => orderEvidence(order, i)), ...lines.map((l) => ruleEvidence(l, ctx.pack)).filter((e): e is Evidence => !!e)];
    const heldText = held.length ? ` ${held.map((h) => `Nothing in the policy pack refunds ${h.short ?? h.label} on what I have; held.`).join(" ")}` : "";
    const tech = [...checkTech(), ...lines.map(lineTech), packTech];
    offer = newOffer;
    vars.offer = offer.total;

    if (escalated.length) {
      termination = { kind: "escalate_staff", reason: `policy gap: ${escalated.map((l) => l.label).join(", ")}`, sla: "a BJ's specialist replies within one business day" };
      const checked = escalated.map((l) => runs[l.issue]).flatMap((r) => r.checks.map((k) => `${k.rule_id} ${k.fired ? "fired" : k.missing.length ? `missing ${k.missing.join(", ")}` : "did not fire"}`));
      const confirmedWhat = check ? check.decision.intent.diffs.filter((d) => d.severity === "material").map((d) => `${d.dimension} ${d.cart}`).join(", ") : "";
      push("umpyr", "escalate", `${check ? `The Check record shows the member ${check.link.confirmation_by === "member" ? "personally confirmed" : "did not personally confirm"} ${confirmedWhat || "the order as mandated"} before payment. ` : ""}No published clause covers ${escalated.map((l) => short(l.issue)).join(", ")}, so I will not guess. A BJ's specialist reviews it with this evidence attached; ${termination.sla}. The gap is logged for BJ's policy owner.${approved.length ? ` Settled meanwhile: ${approved.map(say).join(", ")}.` : ""}`, evidence, position, true, [...checkTech(), ...checked.map((x) => `rule ${x}`), packTech]);
      push("muse", "acknowledge", fill(texts.acknowledge ?? "Understood. I will tell the member a person is reviewing it and relay the decision.", vars), [], "n/a");
      break;
    }

    if (needFact.length) {
      const asksFor = needFact.length === 1 ? runs[needFact[0].issue].ruling.missing_facts.map(friendly).join(" and ") : needFact.map((l) => `for ${short(l.issue)}, ${runs[l.issue].ruling.missing_facts.map(friendly).join(" and ")}`).join("; ");
      push("umpyr", "ask_fact", `I have the Check record and the mandate. ${approved.length ? `So far: ${approved.map(say).join(", ")}. ` : ""}${denied.length ? `${denied.map(say).join(", ")}. ` : ""}To rule on ${needFact.map((l) => short(l.issue)).join(" and ")} I need ${asksFor}.${heldText}`, evidence, position, true, tech);
      prevOffer = offer;
      for (const l of needFact) {
        const key = l.issue;
        const fr = c.muse.facts_on_request?.[key];
        const mf = c.muse.member_facts?.[key];
        const kv = (o: Record<string, unknown>) => Object.entries(o).filter(([k]) => !k.startsWith("_")).map(([k, v]) => `${k} = ${String(v)}`);
        if (fr) {
          overrides[key] = { ...(overrides[key] ?? {}), ...Object.fromEntries(Object.entries(fr).filter(([k]) => !k.startsWith("_"))) };
          push("muse", "provide_fact", fill(String(fr._text ?? `Facts for ${l.label} attached.`), vars), [{ kind: "attachment", ref: `evidence · ${key}`, detail: kv(fr).join(", ") }], "n/a", false, [`${key}: ${kv(fr).join(", ")}`, `attachment sha256 ${vars.photo_hash}`]);
        } else if (mf) {
          referral = { turn: turns.length + 1, resumed: false };
          push("muse", "refer_to_member", fill(String(mf._refer ?? "That is outside my delegation for this order; I am asking the member."), vars), [{ kind: "mandate", ref: order.check?.mandate_id ?? "mandate", detail: "the dispute delegation does not cover this choice; the agent escalates to the customer" }], "n/a", false, ["termination condition: escalate to the customer via their agent (paused)"]);
          overrides[key] = { ...(overrides[key] ?? {}), ...Object.fromEntries(Object.entries(mf).filter(([k]) => !k.startsWith("_"))) };
          referral = { ...referral, resumed: true };
          push("member", "member_decision", fill(String(mf._text ?? "The member decided."), vars), [], "n/a", false, [`${key}: ${kv(mf).join(", ")} · resumed`]);
        } else {
          termination = { kind: "escalate_customer", reason: `the agent cannot supply ${asksFor}`, sla: null };
          push("muse", "acknowledge", "I cannot supply that; I am asking the member and will reopen the case with the answer.", [], "n/a");
        }
      }
      prevDecisions = Object.fromEntries(lines.map((l) => [l.issue, l.decision]));
      continue;
    }

    // every active issue decided
    if (!approved.length && denied.length) {
      const hist = pushbacks ? precedentFor(c.issues[0].category_key, ctx) : null;
      const ev = hist ? [...evidence, { kind: "history" as const, ref: `${hist.count}/${hist.denied} denied cases with goodwill`, detail: hist.exists ? `staff granted goodwill before: ${Object.entries(hist.remedies).map(([k, v]) => `${k} ×${v}`).join(", ")}` : "no precedent of goodwill on this clause; policy stands" }] : evidence;
      if (hist?.exists) {
        termination = { kind: "escalate_staff", reason: "denied by policy, but similar cases were handled differently; a person decides", sla: "a BJ's specialist replies within one business day" };
        push("umpyr", "escalate", `The policy still says ${denied.map(say).join(", ")}, but similar cases have been handled differently in the past, so a BJ's specialist takes a second look.`, ev, "held", true, [...tech, `precedent ${hist.count}/${hist.denied}`]);
        push("muse", "acknowledge", fill(texts.acknowledge ?? "Understood.", vars));
        break;
      }
      const shownLines = check?.link.confirmation?.lines_shown;
      push("umpyr", "deny", pushbacks === 0
        ? `${check ? (check.link.confirmation_by === "member" ? `The step-up showed the member ${shownLines ? "exactly what was in the cart" : "the changed order"}, and the member confirmed it with a signed checkout mandate. ` : `The step-up was answered through ${check.link.confirmation_by.replace("_", " ")}. `) : ""}${denied.map((l) => l.note ?? `${l.label}: ${l.decision}`).join(" ")} Fault: ${who(denied[0].fault)}. I cannot offer what the policy pack does not license.`
        : `I have re-read the mandate and the Check record; my position stands.${hist && !hist.exists ? " There is no precedent of goodwill on this clause in BJ's closed cases, so I am not escalating." : ""} The member keeps every right under the published policy; nothing beyond it is on offer.`,
        ev, pushbacks === 0 ? "opening" : "held", true, pushbacks === 0 ? [...checkTech(), ...(shownLines ? shownLines.map((x) => `shown to the member: ${x}`) : []), ...denied.map(lineTech), packTech] : [...denied.map(lineTech), hist ? `precedent: ${hist.count} goodwill of ${hist.denied} denied` : "", packTech].filter(Boolean));
      if (pushbacks < (c.muse.pushback?.length ?? 1)) {
        push("muse", "pushback", fill(c.muse.pushback?.[pushbacks] ?? "The member asks you to reconsider: the mandate shows what was intended, and the outcome is disproportionate.", vars), [{ kind: "mandate", ref: order.check?.mandate_id ?? "mandate", detail: "the member's instruction as signed" }], "held", false, [`cites mandate ${order.check?.mandate_id ?? ""}`]);
        pushbacks++;
        prevOffer = offer; prevDecisions = Object.fromEntries(lines.map((l) => [l.issue, l.decision]));
        continue;
      }
      termination = { kind: "ruling_accepted", reason: "the agents agree on the ruling: no remedy under policy", sla: null };
      push("muse", "acknowledge", fill(texts.acknowledge ?? "Understood. I will record the ruling and the clause against the order and inform the member.", vars), [], "accepted");
      break;
    }

    // money on the table: PDP
    const needsApproval = approved.some((l) => runs[l.issue].pdp.decision === "needs_approval") && !staffApproved;
    ceilingHit = ceilingHit || approved.some((l) => runs[l.issue].pdp.decision === "needs_approval");
    if (needsApproval) {
      const reasons = [...new Set(approved.flatMap((l) => runs[l.issue].pdp.reasons))];
      termination = { kind: "escalate_staff", reason: `approval: ${reasons.join("; ")}`, sla: "BJ's approver, within one business day" };
      push("umpyr", "needs_approval", `${approved.map(say).join(", ")}${denied.length ? `; ${denied.map(say).join(", ")}` : ""}; fault ${who(approved[0].fault)}. $${offer.total} is above what I can approve alone, so a BJ's approver signs off before I issue the terms; ${termination.sla}.${heldText}`, evidence, position, true, [...tech, `PDP ${runs[approved[0].issue].pdp.action} $${offer.total} → needs_approval: ${reasons.join("; ")}`]);
      if (opts.staff_approved) {
        staffApproved = true;
        termination = null;
        push("bjs_staff", "staff_approval", `Approved: $${offer.total} ${approved[0].remedy} on ${order.order_id}, fault ${who(approved[0].fault)}. Proceed to terms.`, [{ kind: "policy_rule", ref: `PDP · authorize(umpyr, ${runs[approved[0].issue].pdp.action}, ${order.order_id}, $${offer.total})`, detail: "needs_approval → approved by BJ's staff" }], "n/a", false, [`PDP needs_approval → approved by BJ's staff · ${approved.map((l) => l.rule_id).filter(Boolean).join(", ")}`]);
        prevOffer = offer; prevDecisions = Object.fromEntries(lines.map((l) => [l.issue, l.decision]));
        continue;
      }
      push("muse", "acknowledge", fill(texts.wait ?? texts.acknowledge ?? "Understood. I will inform the member and wait for BJ's.", vars));
      break;
    }

    if (!accepted) {
      const kind: TurnKind = prevOffer && prevOffer.total === offer.total && !conceded ? "hold" : "offer";
      push("umpyr", kind, `${position === "conceded" ? "You are right about that on those facts. " : position === "changed_on_evidence" ? "With that evidence the ruling changes. " : ""}${approved.map(say).join(", ")}${denied.length ? `; ${denied.map(say).join(", ")}` : ""}. Total $${offer.total}; fault ${[...new Set(approved.map((l) => who(l.fault)))].join(" and ")}.${heldText}${held.length ? "" : ` ${staffApproved ? "BJ's has approved. " : ""}If the member accepts I will issue the terms.`}`, evidence, position, true, tech);
      prevOffer = offer; prevDecisions = Object.fromEntries(lines.map((l) => [l.issue, l.decision]));
      // Muse's move
      const arguable = held.filter((h) => c.muse.facts_on_counter?.[h.key]);
      if (arguable.length && counterRounds < (c.muse.counter_rounds ?? 1)) {
        counterRounds++;
        for (const h of arguable) {
          const fc = c.muse.facts_on_counter![h.key];
          const facts = Object.fromEntries(Object.entries(fc).filter(([k]) => !k.startsWith("_")));
          overrides[h.key] = { ...(overrides[h.key] ?? {}), ...facts };
          active.add(h.key);
          push("muse", "counter", fill(String(fc._text ?? `On ${h.label}: I ask you to look again.`), vars), [orderEvidence(order, h), ...(check ? [checkEvidence(check)[0]] : [])], "held", false, [`${h.key}: ${Object.entries(facts).map(([k, v]) => `${k} = ${String(v)}`).join(", ")}`]);
        }
        continue;
      }
      if (held.length) {
        referral = { turn: turns.length + 1, resumed: false };
        push("muse", "refer_to_member", `Your offer is within my delegation, but dropping ${held.map((h) => h.short ?? h.label).join(", ")} is not. I am asking the member.`, [], "n/a", false, ["termination condition: escalate to the customer via their agent (paused)"]);
        for (const h of held) asks.delete(h.key);
        referral = { ...referral, resumed: true };
        accepted = true;
        push("member", "member_decision", `Accept the $${offer.total}; drop ${held.map((h) => h.short ?? h.label).join(", ")}.`, [], "accepted", false, ["resumed"]);
        continue;
      }
      accepted = true;
      push("muse", "accept", fill(texts.accept ?? "Accepted for the member: ${offer}. Please issue the terms.", vars), [], "accepted");
      continue;
    }

    // accepted: terms, signature, execution, audit
    if (!termsIssued) {
      const termsObj = { case_id: c.case_id, order_id: order.order_id, member_token: order.member.member_token, agent_id: c.complaint.agent.agent_id, mandate_id: order.check?.mandate_id ?? null, check_decision_id: check?.decision.decision_id ?? null, lines: approved.map((l) => ({ issue: l.issue, item_id: c.issues.find((i) => i.key === l.issue)?.extracted.item_id, remedy: l.remedy, amount: l.amount, rule_id: l.rule_id, clause_id: l.clause_id, fault: l.fault })), denied: denied.map((l) => ({ issue: l.issue, rule_id: l.rule_id, fault: l.fault })), total: offer.total, currency: "USD", rule_pack_version: ctx.pack.version, settlement: approved.map((l) => l.note).filter(Boolean), valid_for_hours: 72 };
      const hash = sha256Hex(canonical(termsObj));
      terms = { terms: termsObj, hash, status: "awaiting", confirmation: null };
      vars.terms_hash = `${hash.slice(0, 16)}…`;
      execution = { status: opts.mode === "shadow" ? "shadow" : "waiting_confirmation", request: null, response: null };
      termsIssued = true;
      push("umpyr", "terms", `Terms for signature: $${offer.total} to the original card (${approved.map(say).join(", ")}), valid 72 hours. Nothing settles before you sign the hash.`, [{ kind: "attachment", ref: `terms sha256 ${hash.slice(0, 16)}…`, detail: "canonical JSON, sorted keys; the member's agent signs exactly this hash" }], "held", true, [`terms_hash sha256 ${hash}`, `clauses ${[...new Set(approved.map((l) => l.clause_id))].join(", ")} · ${packTech} · valid 72 h`]);
      customerConfirmed = true;
      terms = { ...terms, status: "confirmed", confirmation: { protocol: c.complaint.normalized, type: "terms_accepted", case_id: c.case_id, terms_hash: hash, signed_by: "member delegation key via the agent", kid: check?.request.mandate.signature.kid ?? null, at: "2026-10-29T10:12:40Z" } };
      push("muse", "terms_accepted", fill(texts.accept_terms ?? "terms_accepted. Hash verified against the terms you sent; signed with the member's delegation key.", vars), [{ kind: "attachment", ref: "terms_accepted", detail: `hash ${hash.slice(0, 16)}… · kid ${check?.request.mandate.signature.kid ?? "member key"}` }], "accepted", false, [`terms_accepted · terms_hash ${hash.slice(0, 16)}… · kid ${check?.request.mandate.signature.kid ?? "member key"}`]);
      continue;
    }

    if (opts.mode === "shadow") {
      execution = { status: "shadow", request: null, response: null };
      termination = { kind: "agreement", reason: "shadow mode: agreement recorded, nothing executes", sla: null };
      push("bjs", "execute", "Shadow mode: the agreed terms are recorded against the order; no refund request is sent to BJ's.", [], "n/a", false, ["execution: shadow"]);
    } else {
      const endpoint = `POST /v1/orders/${order.order_id}/refunds`;
      const idem = sha256Hex(`${c.case_id}:${terms!.hash}`).slice(0, 24);
      const request = { endpoint, idempotency_key: idem, body: { order_id: order.order_id, member_token: order.member.member_token, lines: approved.map((l) => ({ item_id: c.issues.find((i) => i.key === l.issue)?.extracted.item_id, action: l.remedy, amount: l.amount, reason_clause: l.clause_id })), total: offer.total, currency: "USD", terms_hash: terms!.hash, collect: approved.filter((l) => l.fault === "agent").map((l) => ({ item_id: c.issues.find((i) => i.key === l.issue)?.extracted.item_id, note: "unopened cases collected at no charge" })) } };
      const response = { status: "accepted", merchant_reference: `BJS-${sha256Hex(idem).slice(0, 10).toUpperCase()}`, settles_via: order.payment_method === "bjs_card" ? "BJ's co-brand card credit" : "original card", expected_posting: "3-5 business days (illustrative)", executed_by: "BJ's order service (mock)" };
      execution = { status: "executed", request, response };
      termination = { kind: "agreement", reason: `terms ${terms!.hash.slice(0, 12)}… accepted by the member's agent; BJ's executed`, sla: null };
      push("bjs", "execute", `BJ's accepted the refund of $${offer.total} to the ${response.settles_via}${request.body.collect.length ? " and scheduled collection of the unopened cases" : ""}; it posts in 3 to 5 business days. Money never touches Umpyr.`, [{ kind: "attachment", ref: `idempotency ${idem}`, detail: "one request per terms hash; a retry returns the same reference" }], "n/a", false, [`${endpoint} → accepted · ref ${response.merchant_reference}`, `idempotency ${idem} (one request per terms hash)`]);
    }
    break;
  }

  // audit record: the whole negotiation, hashed and signed
  const faults = Object.fromEntries(Object.entries(runs).map(([k, r]) => [k, r.ruling.fault]));
  const headline: Fault = offer.lines.length ? ([...offer.lines].sort((a, b) => Number(b.amount ?? 0) - Number(a.amount ?? 0))[0].fault) : "indeterminate";
  const record = {
    case_id: c.case_id, order_id: order.order_id, workspace: "bjs", channel: "agent", adapter: adapter.id, agent_platform: c.complaint.agent.platform, agent_id: c.complaint.agent.agent_id,
    check_decision_id: check?.decision.decision_id ?? null, check_hash: check?.decision.commitment.hash ?? null, mandate_id: order.check?.mandate_id ?? null, confirmation_by: order.check?.confirmation_by ?? null, what_if: check?.what_if ?? false,
    rule_pack_version: ctx.pack.version,
    issues: Object.entries(runs).map(([k, r]) => ({ issue: k, rule_id: r.ruling.winning_rule_id, decision: r.ruling.decision, remedy: r.ruling.remedy, amount: r.ruling.amount, fault: r.ruling.fault, audit_hash: r.audit.hash })),
    turns: turns.map((t) => ({ n: t.n, speaker: t.speaker, kind: t.kind, position: t.position, offer_total: t.offer?.total ?? null })),
    fault: headline, terms_hash: terms?.hash ?? null, terms_status: terms?.status ?? "n/a", execution: { status: execution.status, merchant_reference: (execution.response as { merchant_reference?: string } | null)?.merchant_reference ?? null },
    termination, mode: opts.mode, auto_approve_limit: opts.auto_approve_limit, pii_masked: true, is_synthetic: true,
  };
  const hash = sha256Hex(canonical(record));
  const signature = hmacSha256Hex(DEMO_SIGNING_KEY, hash);
  const final = state();
  const closing = termination?.kind === "agreement" ? "Case closed by agreement." : termination?.kind === "ruling_accepted" ? "Case closed: ruling accepted." : termination?.kind === "escalate_staff" ? "Case open with BJ's staff." : "Case paused with the member.";
  turns.push({ n: turns.length + 1, speaker: "umpyr", kind: "audit", text: `${closing} The audit record is signed.`, tech: [`audit sha256 ${hash.slice(0, 16)}… · HMAC-SHA256 kid ${DEMO_KID}`, `covers the Check record, the mandate, ${Object.keys(runs).length} ruling${Object.keys(runs).length === 1 ? "" : "s"} and ${turns.length} turns`], evidence: [{ kind: "attachment", ref: `audit ${hash.slice(0, 16)}…`, detail: `signature ${signature.slice(0, 16)}…` }], position: "n/a", offer: offer.lines.length ? offer : null, raci: raciFor("audit", headline), state: final });

  const leadKey = c.issues.find((i) => runs[i.key])?.key ?? null;
  return { case: c, order, check, turns, final, fault: { headline, by_issue: faults }, terms, execution, audit: { record, hash, signature, kid: DEMO_KID }, runs, lead_run: leadKey ? runs[leadKey] : null, umpyr_turn_count: turns.filter((t) => t.speaker === "umpyr").length };
}

export const SPEAKER_LABEL: Record<Speaker, string> = { muse: "Muse (member's agent)", umpyr: "Umpyr agent (for BJ's)", bjs: "BJ's order service", bjs_staff: "BJ's approver", member: "Member, via Muse" };
export const POSITION_LABEL: Record<Position, string> = { opening: "opening position", held: "held", conceded: "conceded", changed_on_evidence: "changed on new evidence", accepted: "accepted", "n/a": "" };
export const TERMINATIONS: { kind: Termination["kind"]; label: string }[] = [
  { kind: "agreement", label: "agreement: terms hash signed by the member's agent, BJ's executes" },
  { kind: "escalate_staff", label: "escalate to BJ's staff: policy gap, or amount above the auto-approve ceiling" },
  { kind: "escalate_customer", label: "escalate to the customer via their agent: a choice outside the agent's delegation" },
];
