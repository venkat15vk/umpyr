/**
 * Chat layer for the BJ's Resolve demo: turns free text (or a fixture ticket) into a Ticket, runs the deterministic
 * engine, and maps the run onto the same `Ops` shape the Rockler and Alaska pages render (OpsView), so the ops
 * narrative is identical: Classification, Neighbour cases, Plan, Procedure, Ruling, Governance.
 *
 * Everything here is deterministic over samples/bjs fixtures: the router is keyword-weighted, the extractor is
 * regex-based, neighbours are token Jaccard over samples/bjs/history.json. No model calls.
 */
import type { Citation, CustomerBlock, Neighbour, Ops, Plan, Step, TurnDelta } from "../resolve";
import { runCase, type ResolveRun, type RunOptions } from "./resolveEngine";
import { canonical, sha256Hex } from "./sha256";
import type { Category, Claim, Clause, Order, Procedure, RulePack, Ticket } from "./types";

export type HistoryCase = {
  ticket_id: string;
  category_key: string;
  channel: "email" | "agent";
  opened_at: string;
  hours_to_resolve: number;
  reason: string;
  summary: string;
  member_token: string;
  steps: string[];
  policy: { decision: string; remedy: string | null; amount: string | null; rule_id: string | null };
  staff_outcome: { decision: string; remedy: string | null; amount: string | null };
  is_drift: boolean;
  drift_reason: string | null;
  is_synthetic: true;
};
export type HistoryFile = { workspace: string; version: string; note: string; cases: HistoryCase[] };

export type Ctx = { pack: RulePack; procedures: Procedure[]; orders: Order[]; categories: Category[]; history: HistoryFile };

// ---------- router (keyword-weighted; mirrors the engine's router confidence contract: < 0.70 is a fallback) ----------

const KEYWORDS: Record<string, [string, number][]> = {
  sdd_missing_substituted: [["same-day", 3], ["same day", 3], ["delivery", 2], ["delivered", 2], ["missing", 3], ["not in", 2], ["wasn't in", 2], ["weren't in", 2], ["substitut", 4], ["swapped", 3], ["driver", 2], ["left out", 2], ["never arrived", 2], ["bags", 1]],
  perishable_damaged: [["spoil", 4], ["rotten", 3], ["mold", 3], ["smell", 3], ["damaged", 2], ["crushed", 3], ["leak", 3], ["expired", 2], ["past date", 3], ["thaw", 3], ["broken", 1], ["salmon", 1], ["berries", 1], ["strawberr", 1], ["chicken", 1], ["eggs", 1], ["milk", 1], ["meat", 1], ["beef", 1], ["went bad", 3]],
  pickup_issue: [["pickup", 3], ["pick up", 3], ["pick-up", 3], ["curbside", 4], ["not ready", 3], ["ready late", 3], ["waited", 2], ["checked in", 2], ["hold window", 2], ["collect", 2], ["handling fee", 2], ["pickup fee", 3], ["wasn't ready", 3]],
  coupon_price_adjust: [["coupon", 4], ["clipped", 3], ["price adjust", 4], ["price drop", 4], ["on sale", 3], ["expresspay", 2], ["advertised", 2], ["lower price", 3], ["discount", 2], ["price match", 3], ["difference", 1], ["cheaper", 2], ["dropped", 1]],
  membership_renewal: [["membership", 4], ["renew", 4], ["auto-renew", 4], ["annual fee", 3], ["club+", 2], ["membership fee", 3], ["cancel my membership", 3]],
  return_eligibility: [["return", 3], ["take back", 2], ["change of mind", 3], ["don't need", 2], ["no longer", 2], ["unopened", 2], ["90 days", 2], ["receipt", 1], ["return policy", 3], ["bring back", 2], ["tv", 1], ["cookware", 1]],
  agent_order_error: [["agent", 3], ["assistant", 2], ["my ai", 3], ["mandate", 3], ["cases of", 2], ["instead of", 2], ["meant to", 2], ["too many", 2], ["by mistake", 2], ["shopping agent", 3], ["bot", 2], ["ordered for me", 3], ["on my behalf", 3]],
  duplicate_charge_hold: [["charged twice", 4], ["double charge", 4], ["duplicate", 4], ["two charges", 4], ["pending charge", 3], ["pre-auth", 4], ["preauth", 4], ["authorization", 2], ["hold", 3], ["pump", 3], ["gas", 1], ["statement", 2], ["twice", 3], ["card", 1]],
};

export type Classification = { category_key: string; confidence: number; source: string; probabilities: Record<string, number>; secondary: string | null };

export function findOrderId(text: string): string | null {
  const m = text.match(/\bBJ[-\s]?(\d{4})\b/i);
  return m ? `BJ-${m[1]}` : null;
}

function hintFromOrder(order: Order | null): string | null {
  if (!order) return null;
  if (order.items.some((i) => i.department === "membership")) return "membership_renewal";
  if (order.placed_by === "agent") return "agent_order_error";
  switch (order.fulfilment.method) {
    case "same_day_delivery": return "sdd_missing_substituted";
    case "club_pickup": case "curbside": return "pickup_issue";
    case "gas": return "duplicate_charge_hold";
    default: return "return_eligibility";
  }
}

export function classify(text: string, order: Order | null): Classification {
  const t = text.toLowerCase();
  const scores: Record<string, number> = {};
  for (const [cat, kws] of Object.entries(KEYWORDS)) scores[cat] = kws.reduce((s, [kw, w]) => s + (t.includes(kw) ? w : 0), 0);
  const total = Object.values(scores).reduce((a, b) => a + b, 0);
  const ranked = Object.entries(scores).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const probabilities = Object.fromEntries(ranked.map(([k, v]) => [k, total ? Number((v / total).toFixed(3)) : 0]));
  const [top, topScore] = ranked[0];
  const hint = hintFromOrder(order);
  if (total === 0 || topScore < 3) {
    if (hint) return { category_key: hint, confidence: 0.55, source: "fallback", probabilities, secondary: null };
    return { category_key: "unclassified", confidence: 0, source: "router", probabilities, secondary: null };
  }
  let confidence = Math.min(0.97, 0.5 + topScore / total * 0.5 + Math.min(topScore, 8) * 0.02);
  if (hint === top) confidence = Math.min(0.98, confidence + 0.05);
  const second = ranked[1];
  const secondary = second && second[1] >= 4 && second[1] >= topScore * 0.6 ? second[0] : null;
  return { category_key: top, confidence: Number(confidence.toFixed(2)), source: "router", probabilities, secondary };
}

// ---------- fact extraction (regex; only sets what the text supports) ----------

const WORD_NUM: Record<string, number> = { one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10, twelve: 12 };
const num = (s: string | undefined): number | null => (s === undefined ? null : WORD_NUM[s.toLowerCase()] ?? (Number.isFinite(Number(s)) ? Number(s) : null));

function hoursAgo(t: string): number | null {
  let m: RegExpMatchArray | null;
  if ((m = t.match(/(\d+|an?)\s*(hours?|hrs?)\s*(ago|later|after)/))) return m[1] === "a" || m[1] === "an" ? 1 : Number(m[1]);
  if ((m = t.match(/(\d+|a)\s*(days?)\s*(ago|later|after)/))) return (m[1] === "a" ? 1 : Number(m[1])) * 24;
  if ((m = t.match(/(\d+|a)\s*(weeks?)\s*(ago|later|after)/))) return (m[1] === "a" ? 1 : Number(m[1])) * 168;
  if ((m = t.match(/(\d+|a)\s*(months?)\s*(ago|later|after)/))) return (m[1] === "a" ? 1 : Number(m[1])) * 720;
  if ((m = t.match(/(\d+)\s*(days?)\b/))) return Number(m[1]) * 24;
  if ((m = t.match(/(\d+)\s*(hours?|hrs?)\b/))) return Number(m[1]);
  if (/\b(today|this morning|this afternoon|just now|an hour ago|right away|immediately|at the car|at pickup)\b/.test(t)) return 2;
  if (/\byesterday\b/.test(t)) return 24;
  if (/\blast week\b/.test(t)) return 168;
  if (/\blast month\b/.test(t)) return 720;
  return null;
}

export function reasonFor(text: string, category: string): string {
  const t = text.toLowerCase();
  switch (category) {
    case "sdd_missing_substituted": return /substitut|swapped|different brand|replaced with/.test(t) ? "substituted_item" : "missing_item";
    case "perishable_damaged": return /spoil|rotten|mold|smell|past date|expired|thaw|went bad|sour/.test(t) ? "spoiled" : "damaged";
    case "pickup_issue": return /not collected|didn'?t (pick|collect)|couldn'?t (pick|collect|make)|missed|hold window|cancel/.test(t) ? "pickup_cancelled_late" : "pickup_not_ready";
    case "coupon_price_adjust": return /coupon|clipped/.test(t) ? "coupon_not_applied" : "price_adjustment";
    case "membership_renewal": return "membership_refund";
    case "return_eligibility": return "change_of_mind";
    case "agent_order_error": return "agent_quantity_error";
    case "duplicate_charge_hold": return /pre-?auth|pending|hold|pump|gas/.test(t) ? "preauth_hold" : "duplicate_charge";
    default: return "other";
  }
}

export function matchItem(text: string, order: Order | null): string | null {
  if (!order) return null;
  const t = text.toLowerCase();
  let best: { id: string; score: number } | null = null;
  for (const it of order.items) {
    const toks = it.name.toLowerCase().replace(/[^a-z0-9 ]/g, " ").split(/\s+/).filter((w) => w.length > 2 && !["the", "and", "with", "pack", "case", "count", "class", "set"].includes(w));
    const score = toks.filter((w) => t.includes(w)).length;
    if (score > 0 && (!best || score > best.score)) best = { id: it.item_id, score };
  }
  return best?.id ?? null;
}

/** Facts the text explicitly supports. Undefined means "not stated" so a rule can ask for it. */
export function extractFacts(text: string, category: string, order: Order | null): Partial<Claim> & { item_id?: string } {
  const t = text.toLowerCase();
  const out: Partial<Claim> & { item_id?: string } = {};
  const item = matchItem(text, order);
  if (item) { out.item_id = item; out.item_ids = [item]; }
  if (/photo|picture|pic\b|image|attached|screenshot/.test(t)) out.has_photos = !/no (photo|picture)|didn'?t take|threw (it )?(out|away)|don'?t have (a )?(photo|picture)/.test(t);
  else if (/threw (it )?(out|away)|don'?t have (a )?(photo|picture)|no (photo|picture)/.test(t)) out.has_photos = false;
  const h = hoursAgo(t);
  if (h !== null) out.reported_within_hours = h;
  if (/replace|replacement/.test(t)) out.requested_remedy = "replacement";
  else if (/redeliver|resend|send (the|them|it) again|deliver (the|them|it) again/.test(t)) out.requested_remedy = "redeliver";
  else if (/store credit|credit/.test(t)) out.requested_remedy = "store_credit";
  else if (/price adjust|difference|price match|match the price/.test(t)) out.requested_remedy = "price_adjustment";
  else if (/refund|money back|charge back|reverse/.test(t)) out.requested_remedy = "refund";
  let m: RegExpMatchArray | null;
  if (category === "coupon_price_adjust") {
    if ((m = text.match(/\b(?:coupon|code)\s*#?\s*([A-Z]{2,}-?\d{3,}|[A-Z0-9-]{6,})/i))) out.coupon_id = m[1].toUpperCase();
    else if ((m = text.match(/\b(CPN-\d+)\b/i))) out.coupon_id = m[1].toUpperCase();
    if ((m = t.match(/\$\s?(\d+(?:\.\d{2})?)/)) && /advertis|sign|shelf|listed|flyer|now|sale|dropped|price/.test(t)) out.advertised_price = Number(m[1]).toFixed(2);
  }
  if (category === "membership_renewal") {
    if (/\bcancel/.test(t) && !/don'?t (want to )?cancel|not cancel|keep/.test(t)) out.wants_cancel_membership = true;
    else if (/keep (my |the )?membership|don'?t (want to )?cancel|not cancel|stay a member/.test(t)) out.wants_cancel_membership = false;
    if (/haven'?t (shopped|used|been|visited|bought)|not (shopped|used|been|visited)|no (purchases|visits)|never used/.test(t)) out.used_benefits_since_renewal = false;
    else if (/(have |already )?(shopped|used it|visited|been to the club|bought)/.test(t)) out.used_benefits_since_renewal = true;
  }
  if (category === "agent_order_error") {
    if ((m = t.match(/(?:meant|intended|wanted|asked for|supposed to (?:order|get|be)|told it|said)\s*(?:to (?:order|get|buy))?\s*(\d+|one|two|three|four|five|six|ten|twelve)\b/))) out.quantity_intended = num(m[1]) ?? undefined;
    const it = order?.items.find((i) => i.item_id === item) ?? order?.items[0];
    if ((m = t.match(/(?:got|received|delivered|arrived|sent|ordered)\s*(?:me\s*)?(\d+|one|two|three|four|five|six|ten|twelve)\s*(cases?|packs?)?/))) {
      const n = num(m[1]) ?? 0;
      out.quantity_received = m[2]?.startsWith("case") && it?.units_per_case ? n * it.units_per_case : n;
    }
    if ((m = t.match(/keep(?:ing)?\s*(\d+|one|two|three|four|five|six)\b/))) out.cases_to_keep = num(m[1]) ?? undefined;
    else if (/keep (the )?(rest|them|everything|all)/.test(t)) out.cases_to_keep = 0;
  }
  if (category === "duplicate_charge_hold") {
    if ((m = t.match(/(\d+)\s*(hours?|hrs?)/))) out.hold_age_hours = Number(m[1]);
    else if ((m = t.match(/(\d+)\s*days?/))) out.hold_age_hours = Number(m[1]) * 24;
    else if (/yesterday/.test(t)) out.hold_age_hours = 24;
    else if (/today|this morning|just now/.test(t)) out.hold_age_hours = 6;
    else if (/last week|a week/.test(t)) out.hold_age_hours = 168;
  }
  return out;
}

export function isConfirmation(text: string): boolean {
  return /\b(confirm|accept(ed)?|agreed?|i'?ll take (it|that|the)|go ahead|proceed|sounds good|yes,? please|that works|ok(ay)?,? (do it|go ahead))\b/i.test(text);
}
export function isPushback(text: string): boolean {
  return /unfair|ridiculous|still want|insist|not acceptable|unacceptable|escalat|manager|supervisor|dispute|chargeback|complain|really\?|seriously|come on/i.test(text);
}

const LIVE_PREFIX = "BJ-T-LIVE-";

/** Build a synthetic ticket from free text (no PII leaves the browser; masking happens on intake in the engine). */
export function ticketFromText(text: string, channel: "email" | "agent", ctx: Ctx, turnTexts: string[] = []): { ticket: Ticket; classification: Classification; order: Order | null } {
  const all = [...turnTexts, text].join("\n");
  const orderId = findOrderId(all);
  const order = orderId ? ctx.orders.find((o) => o.order_id === orderId) ?? null : null;
  const cls = classify(all, order);
  const category = cls.category_key;
  const reason = reasonFor(all, category);
  const facts = extractFacts(all, category, order);
  const item_id = facts.item_id ?? (category === "membership_renewal" ? "L1" : order?.items[0]?.item_id ?? "L1");
  const id = `${LIVE_PREFIX}${sha256Hex(turnTexts[0] ?? text).slice(0, 6).toUpperCase()}`;
  const subject = text.split(/\n|\. /)[0].slice(0, 80);
  const ticket: Ticket = {
    ticket_id: id,
    category_key: category,
    channel,
    created_at: "2026-10-29T10:00:00Z",
    subject,
    order_id: orderId ?? "(none given)",
    thread: [{ role: "customer", at: "2026-10-29T10:00:00Z", text }],
    ...(channel === "agent" ? { agent_message: { protocol: "umpyr-resolve/0.2", type: "claim", agent: { platform: "Unknown", agent_id: "agent_live" }, member_token: order?.member.member_token ?? "mem_unknown", order_id: orderId ?? "", claim: { reason, text: "(relayed by the member's agent)" } } } : {}),
    extracted: { ...facts, item_id, item_ids: facts.item_ids ?? [item_id], reason, description: subject },
    is_synthetic: true,
  };
  return { ticket, classification: cls, order };
}

export const isLiveTicket = (t: Ticket) => t.ticket_id.startsWith(LIVE_PREFIX);

// ---------- neighbours + plan (token Jaccard over history.json; the engine uses embeddings) ----------

const tokens = (s: string) => new Set(s.toLowerCase().replace(/[^a-z0-9 ]/g, " ").split(/\s+/).filter((w) => w.length > 2));
function jaccard(a: Set<string>, b: Set<string>): number {
  let inter = 0;
  for (const w of a) if (b.has(w)) inter++;
  const union = a.size + b.size - inter;
  return union ? inter / union : 0;
}

export function caseText(ticket: Ticket, run: ResolveRun): string {
  const c = run.facts.claim;
  const parts = [`category: ${ticket.category_key}`, `intents: ${c.requested_remedy ?? "unknown"}`, `reason: ${c.reason}`, `requested: ${c.requested_remedy ?? "unknown"}`];
  if (run.facts.item?.condition) parts.push(`item_condition: ${run.facts.item.condition}`);
  if (c.has_photos !== undefined && c.has_photos !== null) parts.push(`photos: ${c.has_photos}`);
  if (c.reported_within_hours !== undefined && c.reported_within_hours !== null) parts.push(`reported_hours: ${c.reported_within_hours}`);
  if (run.order) parts.push(`fulfilment: ${run.order.fulfilment.method}`, `placed_by: ${run.order.placed_by}`);
  return parts.join(" | ");
}

export function neighboursFor(ticket: Ticket, run: ResolveRun, ctx: Ctx, k = 5): Neighbour[] {
  const cat = ctx.categories.find((c) => c.key === ticket.category_key);
  const q = tokens(`${ticket.subject} ${ticket.extracted.description ?? ""} ${run.facts.claim.reason} ${cat?.name ?? ""} ${run.facts.item?.name ?? ""}`);
  const scored = ctx.history.cases.map((h) => {
    let sim = jaccard(q, tokens(`${h.summary} ${h.reason} ${h.category_key.replace(/_/g, " ")}`));
    if (h.category_key === ticket.category_key) sim += 0.35;
    if (h.reason === run.facts.claim.reason) sim += 0.15;
    return { h, sim: Math.min(0.97, Number(sim.toFixed(3))) };
  });
  scored.sort((a, b) => b.sim - a.sim || a.h.ticket_id.localeCompare(b.h.ticket_id));
  return scored.slice(0, k).map(({ h, sim }) => ({ ticket_id: h.ticket_id, similarity: sim, category_key: h.category_key, summary: h.summary, staff_outcome: h.staff_outcome, is_drift: h.is_drift, drift_reason: h.drift_reason, hours_to_resolve: h.hours_to_resolve }));
}

function liveSteps(run: ResolveRun): string[] {
  const tools = [...new Set(run.procedure?.steps.map((s) => s.tool) ?? ["lookup_order", "rule", "close"])];
  switch (run.ruling.decision) {
    case "deny": return tools.filter((t) => !["authorize", "confirm_terms", "merchant_api"].includes(t));
    case "needs_fact": return [...new Set(["lookup_order", "extract_facts", "rule", "close"])];
    case "escalate": return ["lookup_order", "rule", "escalate", "close"];
    default: return tools;
  }
}

export function planFor(ticket: Ticket, run: ResolveRun, neighbours: Neighbour[], ctx: Ctx): Plan {
  const hist = neighbours.map((n) => ctx.history.cases.find((h) => h.ticket_id === n.ticket_id)!).filter(Boolean);
  const seqCount = new Map<string, number>();
  const support: Record<string, number> = {};
  for (const h of hist) {
    seqCount.set(h.steps.join(">"), (seqCount.get(h.steps.join(">")) ?? 0) + 1);
    for (const s of h.steps) support[s] = (support[s] ?? 0) + 1;
  }
  const top = [...seqCount.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0];
  const historical = top ? top[0].split(">") : [];
  const agreement = hist.length ? (top?.[1] ?? 0) / hist.length : 0;
  const meanSim = neighbours.length ? Number((neighbours.reduce((s, n) => s + n.similarity, 0) / neighbours.length).toFixed(3)) : null;
  const confident = agreement >= 0.6 && (meanSim ?? 0) >= 0.5;
  const live = liveSteps(run);
  const missing_in_live = historical.filter((s) => !live.includes(s));
  const extra_in_live = live.filter((s) => !historical.includes(s));
  const kind = !historical.length ? "no_history" : missing_in_live.length === 0 && extra_in_live.length === 0 ? "agree" : "diverge";
  const novel = neighbours.length === 0 || (meanSim ?? 0) < 0.35;
  const chosen = kind === "agree" && confident ? "historical" : "live";
  return {
    case_text: caseText(ticket, run),
    historical: { k: hist.length, steps: historical, support, agreement: Number(agreement.toFixed(2)), mean_similarity: meanSim, confident, neighbours: hist.map((h, i) => ({ ticket_id: h.ticket_id, similarity: neighbours[i].similarity, steps: h.steps })) },
    live: { raw: live, steps: live, rationale: `Deterministic planner: steps come from the approved procedure${run.procedure ? ` "${run.procedure.name}" v${run.procedure.version}` : ""}, trimmed to the ruling (${run.ruling.decision}).` },
    comparison: { kind, missing_in_live, extra_in_live },
    novel,
    chosen,
    steps: chosen === "historical" ? historical : live,
    disagreement: kind === "diverge" && confident,
  };
}

// ---------- Ops mapping ----------

export function caseStatus(run: ResolveRun): string {
  switch (run.ruling.decision) {
    case "deny": return "denied";
    case "needs_fact": return "needs_fact";
    case "escalate": return "escalated";
    default:
      if (run.pdp.decision === "recorded") return "recorded";
      if (run.execution.status === "waiting_approval") return "pending_approval";
      if (run.execution.status === "waiting_confirmation") return "awaiting_confirmation";
      return "resolved";
  }
}

function citationOf(c: Clause): Citation {
  return { clause_id: c.clause_id, title: c.title, document: c.source_title, document_version: null, section: c.section ?? null, page: null, span: null, text_hash: sha256Hex(c.text).slice(0, 16), url: c.source_url, tier: c.tier, public: c.confirmed, quote: c.text };
}

function stepStatus(kind: string, present: boolean, run: ResolveRun): { status: string; detail: string } {
  const money = run.ruling.decision === "approve" || run.ruling.decision === "partial";
  switch (kind) {
    case "gather": case "verify": return present ? { status: "ok", detail: "facts on file" } : { status: "needs_fact", detail: "asked the member" };
    case "check": case "decide": return run.ruling.winning_rule_id ? { status: "fired", detail: `${run.ruling.winning_rule_id} → ${run.ruling.decision}` } : run.ruling.decision === "needs_fact" ? { status: "needs_fact", detail: run.ruling.missing_facts.join(", ") } : { status: "evaluated", detail: run.ruling.explanation };
    case "authorize": return !money ? { status: "n/a", detail: "no money moves" } : run.pdp.decision === "allow" ? { status: "ok", detail: run.pdp.reasons.join("; ") } : run.pdp.decision === "needs_approval" ? { status: run.execution.status === "waiting_approval" ? "pending" : "ok", detail: run.pdp.reasons.join("; ") } : { status: "evaluated", detail: run.pdp.reasons.join("; ") };
    case "confirm": return run.terms.status === "n/a" ? { status: "n/a", detail: "nothing to confirm" } : run.terms.status === "confirmed" ? { status: "ok", detail: `terms ${run.terms.hash.slice(0, 12)}… confirmed` } : { status: "pending", detail: `waiting on terms ${run.terms.hash.slice(0, 12)}…` };
    case "execute": return run.execution.status === "executed" ? { status: "ok", detail: (run.execution.request as { endpoint: string }).endpoint } : run.execution.status === "none" || run.execution.status === "shadow" ? { status: "n/a", detail: run.execution.status === "shadow" ? "shadow mode" : "no merchant action" } : { status: "pending", detail: run.execution.status.replace("_", " ") };
    case "close": return run.ruling.decision === "needs_fact" ? { status: "pending", detail: "after the member replies" } : { status: "ok", detail: run.lifecycle_note.slice(0, 80) };
    default: return { status: "placeholder", detail: "" };
  }
}

export type TurnCtx = { turn: number; prev: ResolveRun | null; factsChanged: string[]; messageCategory: Classification | null; pushback: boolean; classification: Classification | null };

export function toOps(run: ResolveRun, ctx: Ctx, t: TurnCtx): Ops {
  const ticket = run.ticket;
  const neighbours = neighboursFor(ticket, run, ctx);
  const plan = planFor(ticket, run, neighbours, ctx);
  const status = caseStatus(run);
  const facts = run.facts as unknown as Record<string, Record<string, unknown>>;
  const clauses = ctx.pack.clauses;
  const citations: Record<string, Citation> = Object.fromEntries(clauses.map((c) => [c.clause_id, citationOf(c)]));
  const steps: Step[] = (run.procedure?.steps ?? []).map((s) => {
    const fields: Record<string, unknown> = {};
    for (const p of s.required_facts) fields[p] = p.split(".").reduce<unknown>((o, k) => (o && typeof o === "object" ? (o as Record<string, unknown>)[k] : undefined), facts);
    const present = s.required_facts.every((p) => fields[p] !== undefined && fields[p] !== null);
    const { status: st, detail } = stepStatus(s.kind, present, run);
    return { step_id: s.step_id, name: s.name, kind: s.kind, tool: s.tool, clause_ids: s.clause_ids, clauses: Object.fromEntries(s.clause_ids.map((id) => [id, clauses.find((c) => c.clause_id === id)?.text ?? ""])), fields, status: st, detail };
  });
  const decisionHash = sha256Hex(canonical({ case_id: run.case_id, ruling: run.ruling, pdp: run.pdp, terms_hash: run.terms.hash }));
  const money = run.ruling.decision === "approve" || run.ruling.decision === "partial";
  const supervisorIssues: string[] = [];
  if (money && run.ruling.amount && !run.reply.includes(`$${run.ruling.amount}`)) supervisorIssues.push("reply omits the amount");
  if (!run.reply.includes(ticket.order_id) && run.order) supervisorIssues.push("reply omits the order reference");
  const escalation = run.ruling.decision === "escalate" ? { type: "policy_gap" as const, reason: run.ruling.explanation } : status === "pending_approval" ? { type: "governance_gate" as const, reason: run.pdp.reasons.join("; ") } : null;
  const status_events: { action: string; message: string }[] = [{ action: "message.received", message: ticket.channel === "agent" ? "structured claim from the member's agent" : "forwarded email" }];
  status_events.push(run.order ? { action: "record.lookup", message: `${run.order.order_id} matched` } : { action: "record.missing", message: `${ticket.order_id} not found` });
  status_events.push({ action: "precedent.retrieved", message: `${neighbours.length} neighbour cases` }, { action: "rule_check", message: run.ruling.winning_rule_id ? `${run.ruling.winning_rule_id} fired` : run.ruling.explanation });
  if (status === "needs_fact") status_events.push({ action: "needs_fact", message: run.ruling.missing_facts.join(", ") });
  if (status === "escalated") status_events.push({ action: "escalation", message: "specialist review" });
  if (status === "pending_approval") status_events.push({ action: "gate.queued", message: run.pdp.reasons.join("; ") });
  if (money && run.pdp.decision === "allow") status_events.push({ action: "gate.allowed", message: run.pdp.reasons.join("; ") });
  if (status === "resolved") status_events.push({ action: "decision.resolved", message: `${run.ruling.remedy} ${run.ruling.amount ? `$${run.ruling.amount}` : ""}`.trim() });
  if (status === "denied") status_events.push({ action: "decision.denied", message: run.ruling.winning_clause_id ?? "" });

  const turn_delta: TurnDelta | undefined = t.turn > 1 ? {
    turn: t.turn,
    message_category: t.messageCategory ? { category_key: t.messageCategory.category_key === "unclassified" ? null : t.messageCategory.category_key, confidence: t.messageCategory.confidence, new_category: t.messageCategory.category_key !== ticket.category_key && t.messageCategory.category_key !== "unclassified" && t.messageCategory.confidence >= 0.7 } : null,
    intents_added: t.prev && run.facts.claim.requested_remedy !== t.prev.facts.claim.requested_remedy && run.facts.claim.requested_remedy ? [run.facts.claim.requested_remedy] : [],
    facts_changed: t.factsChanged,
    status_before: t.prev ? caseStatus(t.prev) : null,
    status,
    ruling_changed: !!t.prev && (t.prev.ruling.decision !== run.ruling.decision || t.prev.ruling.winning_rule_id !== run.ruling.winning_rule_id || t.prev.ruling.amount !== run.ruling.amount),
    plan_comparison: plan.comparison.kind,
    pushback: t.pushback,
    uncovered_ask: null,
    precedent: t.pushback && status === "denied" ? precedentFor(ticket.category_key, ctx) : null,
  } : undefined;

  return {
    turn: t.turn,
    turn_delta,
    status,
    classification: { category_key: ticket.category_key, category_name: run.category.name, confidence: t.classification?.confidence ?? 0.9, source: t.classification?.source ?? "router", probabilities: t.classification?.probabilities, multi_issue: !!t.classification?.secondary, secondary_category_key: t.classification?.secondary ?? null },
    neighbours,
    plan,
    procedure: { ref: run.procedure ? `${run.procedure.category_key}@v${run.procedure.version}` : null, name: run.procedure?.name ?? null, steps },
    record: { reference: ticket.order_id, found: !!run.order, summary: run.order ? `${run.order.items.length} line${run.order.items.length === 1 ? "" : "s"}, $${run.order.total}, ${run.order.fulfilment.method.replace(/_/g, " ")}${run.order.fulfilment.partner ? ` via ${run.order.fulfilment.partner}` : ""}, placed by ${run.order.placed_by}, member ${run.order.member.tier === "club_plus" ? "Club+" : "Club"} ${run.order.member.member_token}` : null },
    facts,
    ruling: { decision: run.ruling.decision, remedy: run.ruling.remedy, amount: run.ruling.amount, fault: run.ruling.fault, winning_rule_id: run.ruling.winning_rule_id, winning_clause_id: run.ruling.winning_clause_id, clause_ids: run.ruling.clause_ids, missing_facts: run.ruling.missing_facts, explanation: run.ruling.explanation, conflicts: [], status, clause_notes: [], citations: run.ruling.clause_ids.map((id) => citations[id]).filter(Boolean), escalation, gap: run.ruling.decision === "escalate" ? { type: "policy_gap", reason: run.ruling.explanation, asks: [] } : null },
    citations,
    case: { id: run.case_id, status, turns: t.turn, event_count: run.steps.length + t.turn, head_hash: run.audit.hash, decision_hash: decisionHash, cost_usd: "0.0000", turn_cost_usd: "0.0000", llm_calls: 0, escalation },
    status_events,
    checks: run.checks.map((c) => ({ rule_id: c.rule_id, clause_id: c.clause_id, tier: c.tier, priority: c.priority, fired: c.fired, missing: c.missing, fields: c.fields })),
    authorization: { decision: run.pdp.decision, action: run.pdp.action, reasons: run.pdp.reasons, amount: run.pdp.amount, limit: run.pdp.limit.toFixed(2) },
    supervisor: { approved: supervisorIssues.length === 0, issues: supervisorIssues },
    governance: { pii_masked: true, rule_pack_version: run.pack_version, procedure: run.procedure ? `${run.procedure.category_key}@v${run.procedure.version}` : null, authority: `pdp ${run.pdp.decision}`, supervisor_check: supervisorIssues.length ? "flagged" : "passed", audit_hash: run.audit.hash, is_synthetic: true, decision_hash: decisionHash, check: run.order?.check ? run.order.check.decision : undefined },
    tools: run.steps.map((s) => ({ tool: s.id, ok: s.status !== "blocked", detail: s.summary })),
    agent_message: run.agent_reply,
  };
}

export function precedentFor(category: string, ctx: Pick<Ctx, "history">): TurnDelta["precedent"] {
  const same = ctx.history.cases.filter((h) => h.category_key === category);
  const denied = same.filter((h) => h.policy.decision === "deny");
  const goodwill = denied.filter((h) => h.staff_outcome.decision === "approve");
  const remedies: Record<string, number> = {};
  for (const g of goodwill) remedies[g.staff_outcome.remedy ?? "none"] = (remedies[g.staff_outcome.remedy ?? "none"] ?? 0) + 1;
  return { count: goodwill.length, denied: denied.length, rate: denied.length ? Number((goodwill.length / denied.length).toFixed(2)) : 0, remedies, examples: goodwill.slice(0, 3).map((g) => ({ ticket_id: g.ticket_id, remedy: g.staff_outcome.remedy ?? "none", amount: g.staff_outcome.amount ? Number(g.staff_outcome.amount) : null, summary: g.summary })), exists: goodwill.length > 0 };
}

const STATUS_PAST: Record<string, string> = { "message.received": "Received your message", "record.lookup": "Checked your order", "record.missing": "Could not match an order yet", "precedent.retrieved": "Compared with similar cases", rule_check: "Reviewed what the policy provides", "gate.queued": "Sent for authorisation", "gate.allowed": "Approved", escalation: "Handed to a specialist", needs_fact: "Need a couple more details", "decision.resolved": "Done", "decision.denied": "Reviewed and explained below" };

export function customerBlock(_run: ResolveRun, ops: Ops): CustomerBlock {
  const lines: string[] = [];
  for (const e of ops.status_events ?? []) { const l = STATUS_PAST[e.action]; if (l && !lines.includes(l)) lines.push(l); }
  const cits = (ops.ruling.citations ?? []).filter((c) => c.public && !!c.url && c.url.startsWith("http")).map((c) => ({ document: c.document, section: c.section, page: null, url: c.url, document_version: null, quote: c.quote }));
  return { status_lines: lines, citations: cits, options: [], chosen_remedy: null, choices: [], proactive: [], queue: ops.status === "pending_approval" ? { sla: "within one business day", due_at: null, overdue: false } : ops.status === "escalated" ? { sla: "from a BJ's specialist within one business day", due_at: null, overdue: false } : null };
}

/** The ops narrative for a message that matched nothing: no order, no category. Same shape, so the ops view still renders. */
export function emptyOps(text: string, cls: Classification, orderRef: string | null, turn: number): Ops {
  const hash = sha256Hex(text);
  return {
    turn, status: "needs_fact",
    classification: { category_key: "unclassified", category_name: "Unclassified", confidence: cls.confidence, source: cls.source, probabilities: cls.probabilities },
    neighbours: [], plan: undefined,
    procedure: { ref: null, name: null, steps: [] },
    record: { reference: orderRef, found: false, summary: null },
    facts: {},
    ruling: { decision: "needs_fact", status: "needs_fact", missing_facts: ["order_id", "claim.reason"], explanation: "Nothing to rule on yet: the message names no order and no recognisable issue.", clause_notes: [] },
    checks: [], authorization: null, supervisor: { approved: true, issues: [] },
    governance: { pii_masked: true, rule_pack_version: null, procedure: null, authority: "none", supervisor_check: "passed", audit_hash: hash, is_synthetic: true },
    tools: [{ tool: "intake", ok: true, detail: "message masked" }, { tool: "order_match", ok: false, detail: "no order reference" }, { tool: "classification", ok: false, detail: "no category above threshold" }],
    agent_message: null,
    status_events: [{ action: "message.received", message: "" }, { action: "record.missing", message: "" }, { action: "needs_fact", message: "order number and what happened" }],
  };
}

// ---------- the turn ----------

export type ChatState = { texts: string[]; ticket: Ticket | null; classification: Classification | null; overrides: RunOptions["facts"]; customer_confirmed: boolean; staff_approved: boolean; prev: ResolveRun | null; turn: number };

export const emptyState = (): ChatState => ({ texts: [], ticket: null, classification: null, overrides: {}, customer_confirmed: false, staff_approved: false, prev: null, turn: 0 });

export type TurnResult = { reply: string; ops: Ops; customer: CustomerBlock; run: ResolveRun | null; state: ChatState };

/** One customer turn: free text, or a fixture ticket when `example` is given. Pure: returns the new state. */
export function customerTurn(state: ChatState, text: string, opts: Omit<RunOptions, "facts" | "customer_confirmed" | "staff_approved">, ctx: Ctx, example?: Ticket): TurnResult {
  const turn = state.turn + 1;
  const texts = [...state.texts, text];
  let ticket = state.ticket;
  let classification = state.classification;
  let overrides = { ...state.overrides };
  let messageCategory: Classification | null = null;
  const confirmed = state.customer_confirmed || (turn > 1 && isConfirmation(text));
  const pushback = !!state.prev && state.prev.ruling.decision === "deny" && isPushback(text);

  if (example) {
    ticket = example;
    classification = { category_key: example.category_key, confidence: 0.84 + (parseInt(sha256Hex(example.ticket_id).slice(0, 2), 16) % 14) / 100, source: "router", probabilities: {}, secondary: null };
    overrides = {};
  } else if (!ticket || turn === 1) {
    const built = ticketFromText(text, opts.channel, ctx, []);
    ticket = built.ticket;
    classification = built.classification;
  } else {
    // follow-up: re-extract against the whole thread, keep only what the new message supports
    const order = ctx.orders.find((o) => o.order_id === ticket!.order_id) ?? null;
    messageCategory = classify(text, order);
    const fresh = extractFacts(text, ticket.category_key, order);
    const orderId = findOrderId(text);
    if (orderId && ticket.order_id !== orderId) {
      // the member supplied the order number: rebuild the ticket from the whole thread
      const built = ticketFromText(text, opts.channel, ctx, state.texts);
      ticket = { ...built.ticket, ticket_id: ticket.ticket_id, thread: [...(ticket.thread ?? []), { role: "customer", at: "2026-10-29T10:05:00Z", text }] };
      classification = built.classification;
    } else {
      ticket = { ...ticket, thread: [...(ticket.thread ?? []), { role: "customer", at: "2026-10-29T10:05:00Z", text }] };
    }
    for (const [k, v] of Object.entries(fresh)) if (v !== undefined) (overrides as Record<string, unknown>)[k] = v;
  }

  const factsChanged = Object.keys(overrides).filter((k) => (state.overrides as Record<string, unknown>)[k] !== (overrides as Record<string, unknown>)[k]);
  const next: ChatState = { texts, ticket, classification, overrides, customer_confirmed: confirmed, staff_approved: state.staff_approved, prev: state.prev, turn };

  if (!ticket || ticket.category_key === "unclassified") {
    const ops = emptyOps(text, classification ?? classify(text, null), ticket?.order_id ?? findOrderId(text), turn);
    const reply = `Thanks for reaching out. To look this up we need your order number (it looks like BJ-7xxx) and a line on what went wrong: a missing or substituted delivery item, a damaged or spoiled product, a pickup problem, a coupon or price question, a membership renewal, a return, an order placed by your AI agent, or a charge on your card.`;
    return { reply, ops, customer: { status_lines: ["Received your message", "Need a couple more details"], citations: [], options: [], chosen_remedy: null, choices: [], proactive: [], queue: null }, run: null, state: next };
  }

  const run = runCase(ticket, ctx, { ...opts, facts: overrides, customer_confirmed: confirmed, staff_approved: state.staff_approved });
  const ops = toOps(run, ctx, { turn, prev: state.prev, factsChanged, messageCategory, pushback, classification });
  let reply = run.reply;
  if (!run.order) reply = `Thanks for reaching out. We could not find an order ${ticket.order_id === "(none given)" ? "reference in your message" : ticket.order_id} on this membership. Reply with the order number from your receipt or the app (it looks like BJ-7xxx) and we will pick this up immediately.`;
  else if (pushback && ops.turn_delta?.precedent?.exists) reply = `${run.reply} Because similar cases have been handled differently in the past, a BJ's specialist will take a second look and reply within one business day.`;
  const customer = customerBlock(run, ops);
  if (!run.order) { customer.status_lines = ["Received your message", "Could not match an order yet", "Need a couple more details"]; customer.citations = []; }
  return { reply, ops, customer, run, state: { ...next, prev: run } };
}

/** Re-run the current case after an ops-side action (staff approval, fact edit) without adding a turn. */
export function rerun(state: ChatState, opts: Omit<RunOptions, "facts" | "customer_confirmed" | "staff_approved">, ctx: Ctx): TurnResult | null {
  if (!state.ticket || state.ticket.category_key === "unclassified") return null;
  const run = runCase(state.ticket, ctx, { ...opts, facts: state.overrides, customer_confirmed: state.customer_confirmed, staff_approved: state.staff_approved });
  const ops = toOps(run, ctx, { turn: state.turn, prev: state.prev, factsChanged: [], messageCategory: null, pushback: false, classification: state.classification });
  return { reply: run.reply, ops, customer: customerBlock(run, ops), run, state: { ...state, prev: run } };
}

// ---------- merchant chat (deterministic answers over the fixtures) ----------

export function merchantAnswer(question: string, ctx: Ctx, tickets: Ticket[]): string {
  const q = question.toLowerCase();
  const cases = ctx.history.cases;
  const byCat = (f: (h: HistoryCase) => boolean = () => true) => {
    const m: Record<string, number> = {};
    for (const h of cases.filter(f)) m[h.category_key] = (m[h.category_key] ?? 0) + 1;
    return Object.entries(m).sort((a, b) => b[1] - a[1]);
  };
  const name = (k: string) => ctx.categories.find((c) => c.key === k)?.name ?? k;
  if (/drift/.test(q)) {
    const d = byCat((h) => h.is_drift);
    const total = cases.filter((h) => h.is_drift).length;
    return [`${total} of ${cases.length} closed cases drift from policy (${((100 * total) / cases.length).toFixed(0)}%). By category:`, ...d.map(([k, n]) => `• ${name(k)}: ${n} of ${byCat((h) => h.category_key === k)[0][1]}`), "", "Most common reasons: " + [...new Set(cases.filter((h) => h.drift_reason).map((h) => h.drift_reason!))].slice(0, 4).join("; ") + ".", "Drift is where staff granted something the compiled policy denies, or skipped a step the procedure requires. Each one is a candidate goodwill clause or a training note."].join("\n");
  }
  if (/categor|volume|pain|most common|top/.test(q)) {
    const d = byCat();
    return ["Closed cases by category (synthetic history, last 30 days):", ...d.map(([k, n]) => { const hs = cases.filter((h) => h.category_key === k); const avg = hs.reduce((s, h) => s + h.hours_to_resolve, 0) / hs.length; const drift = hs.filter((h) => h.is_drift).length; return `• ${name(k)}: ${n} cases · median ~${avg.toFixed(1)} h · ${drift} drift`; }), "", "Pain is highest where time-to-resolve and drift are both high: coupon and price adjustments, and anything outside a published clause."].join("\n");
  }
  if (/rule|fire|clause/.test(q)) {
    const m: Record<string, number> = {};
    for (const h of cases) if (h.policy.rule_id) m[h.policy.rule_id] = (m[h.policy.rule_id] ?? 0) + 1;
    for (const t of tickets) { const r = runCase(t, ctx, { channel: t.channel, mode: "autonomous", auto_approve_limit: 100, facts: {}, customer_confirmed: false, staff_approved: false }); if (r.ruling.winning_rule_id) m[r.ruling.winning_rule_id] = (m[r.ruling.winning_rule_id] ?? 0) + 1; }
    const top = Object.entries(m).sort((a, b) => b[1] - a[1]).slice(0, 8);
    const ill = ctx.pack.clauses.filter((c) => !c.confirmed).map((c) => c.clause_id);
    return [`Rules that decide most often (history + the ${tickets.length} open tickets), pack ${ctx.pack.version}:`, ...top.map(([id, n]) => { const r = ctx.pack.rules.find((x) => x.rule_id === id); const c = ctx.pack.clauses.find((x) => x.clause_id === r?.clause_id); return `• ${id} (${r?.tier}) ×${n}: ${r?.description ?? ""}${c && !c.confirmed ? " [illustrative clause]" : ""}`; }), "", `${ctx.pack.clauses.length} clauses, ${ctx.pack.rules.length} rules. Illustrative (not read on bjs.com): ${ill.join(", ")}.`].join("\n");
  }
  if (/agent/.test(q)) {
    const ag = cases.filter((h) => h.channel === "agent").length;
    return [`${ag} of ${cases.length} closed cases arrived through a member's AI agent (${((100 * ag) / cases.length).toFixed(0)}%).`, `${cases.filter((h) => h.category_key === "agent_order_error").length} cases were agent order errors; where the agent acted outside its mandate the fault is "agent" and the signal feeds Umpyr Check for that agent fingerprint.`, "Pre-payment risk and intent on agent-placed orders is the Check product (see Check Risk & Intent)."].join("\n");
  }
  if (/cost|time|metric|resolve|auto/.test(q)) {
    return ["Illustrative pilot targets shown on the strip: 71% of tickets auto-resolved, 3 min 40 s median time to resolution, $0.42 per resolved ticket, 100% of decisions citing their clause.", `Synthetic history: ${cases.length} closed cases, median ${median(cases.map((h) => h.hours_to_resolve)).toFixed(1)} h, ${cases.filter((h) => h.policy.decision === "approve" || h.policy.decision === "deny").length} decided by a compiled rule without a person.`].join("\n");
  }
  if (/source|illustrative|confirm|polic/.test(q)) {
    const conf = ctx.pack.clauses.filter((c) => c.confirmed);
    return [`${conf.length} clauses were read on bjs.com and carry their source URL; ${ctx.pack.clauses.length - conf.length} are illustrative practice rules (marked in every ruling that uses them).`, ...ctx.pack.clauses.map((c) => `• ${c.clause_id} ${c.title}${c.confirmed ? "" : " [illustrative]"}`)].join("\n");
  }
  return `I can answer from BJ's own data: "what drifts most?", "top categories by pain", "which rule fires most?", "how many cases come from agents?", "which clauses are illustrative?". ${cases.length} closed cases, ${tickets.length} open tickets, pack ${ctx.pack.version}.`;
}

function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  return s.length ? s[Math.floor(s.length / 2)] : 0;
}
