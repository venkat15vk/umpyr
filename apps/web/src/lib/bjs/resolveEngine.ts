/**
 * Umpyr Resolve, BJ's demo: deterministic simulation of the resolve pipeline over versioned fixtures.
 *
 * Same stages and the same contracts as the engine (services/engine/umpyr_engine/resolve): rules decide,
 * procedures come from the approved library, the PDP authorises every action, nothing settles without the
 * customer's confirmation of the terms hash, execution is the merchant's, and the audit record is hashed
 * and signed. The FastAPI engine replaces `runCase` behind adapters.ts without UI changes.
 */
import { anonymiseDeep, b, slugFor } from "../brand";
import { apply, collectVars, getVar } from "./jsonlogic";
import { raciFor } from "./raci";
import { canonical, hmacSha256Hex, sha256Hex } from "./sha256";
import type { Category, Claim, Decision, FactBundle, Fault, Item, Order, Procedure, Raci, Rule, RulePack, Ticket, TIER_RANK as _T } from "./types";
import { TIER_RANK } from "./types";

export type StepStatus = "done" | "fired" | "waiting" | "blocked" | "skipped" | "needs_fact";
export type RunStep = { id: string; n: number; name: string; status: StepStatus; raci: Raci; summary: string; detail?: string; data?: unknown };

export type RuleCheck = { rule_id: string; clause_id: string; tier: string; priority: number; fired: boolean; missing: string[]; fields: Record<string, unknown>; description: string };
export type Ruling = { decision: Decision; remedy: string | null; amount: string | null; fault: Fault; winning_rule_id: string | null; winning_clause_id: string | null; clause_ids: string[]; fired_rule_ids: string[]; missing_facts: string[]; explanation: string; note: string | null };

export type RunOptions = {
  channel: "email" | "agent";
  mode: "autonomous" | "assist" | "shadow";
  auto_approve_limit: number;
  facts: Partial<Claim> & { item_id?: string }; // editable facts (overrides on the extracted claim)
  customer_confirmed: boolean;
  staff_approved: boolean;
};

export type ResolveRun = {
  case_id: string;
  ticket: Ticket;
  order: Order | null;
  category: Category;
  facts: FactBundle;
  checks: RuleCheck[];
  ruling: Ruling;
  procedure: Procedure | null;
  pdp: { decision: "allow" | "needs_approval" | "deny" | "recorded"; action: string; amount: string | null; limit: number; reasons: string[] };
  terms: { terms: Record<string, unknown>; hash: string; status: "n/a" | "awaiting" | "confirmed"; confirmation: Record<string, unknown> | null };
  execution: { status: "executed" | "waiting_confirmation" | "waiting_approval" | "none" | "shadow"; request: Record<string, unknown> | null; response: Record<string, unknown> | null };
  audit: { record: Record<string, unknown>; hash: string; signature: string; kid: string };
  steps: RunStep[];
  reply: string;
  agent_reply: Record<string, unknown> | null;
  lifecycle_note: string;
  pack_version: string;
};

export const DEMO_SIGNING_KEY = "umpyr-demo-hmac-key-bjs-2026"; // demo only; production signs with per-tenant Ed25519 keys
export const DEMO_KID = "umpyr-demo-hmac-2026-10";

// ---------- masking ----------

export function maskText(text: string, names: string[] = []): string {
  let t = text.replace(/[\w.+-]+@[\w-]+\.[\w.]+/g, "[email]").replace(/\(?\b\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b/g, "[phone]").replace(/\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b/g, "[card]").replace(/\b\d{1,5}\s+[A-Z][a-z]+\s+(Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Drive|Dr|Place|Pl|Court|Ct)\b[^,\n]*/g, "[address]");
  for (const nm of names) {
    if (!nm) continue;
    t = t.replace(new RegExp(nm.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g"), "[name]");
    const first = nm.split(" ")[0];
    if (first.length > 2) t = t.replace(new RegExp(`\\b${first}\\b`, "g"), "[name]");
  }
  return t;
}

// ---------- rule evaluation (mirrors packages/rules/umpyr_rules/engine.py) ----------

function applicable(rule: Rule, category: string, reason: string | undefined): boolean {
  if (!rule.enabled) return false;
  if (rule.categories.length && !rule.categories.includes(category)) return false;
  if (rule.claim_reasons.length && (!reason || !rule.claim_reasons.includes(reason))) return false;
  return true;
}

function fieldValues(rule: Rule, facts: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const p of [...new Set([...collectVars(rule.when), ...rule.requires])].sort()) out[p] = getVar(facts, p);
  return out;
}

function amountOf(rule: Rule, facts: Record<string, unknown>): string | null {
  const a = rule.effect.amount;
  if (a === null || a === undefined) return null;
  const v = apply(a, facts);
  if (v === null || v === undefined || Number.isNaN(Number(v))) return null;
  return Number(v).toFixed(2);
}

export function evaluate(pack: RulePack, facts: FactBundle, category: string): { checks: RuleCheck[]; ruling: Ruling } {
  const f = facts as unknown as Record<string, unknown>;
  const reason = facts.claim.reason;
  const rules = pack.rules.filter((r) => applicable(r, category, reason)).sort((a, b) => TIER_RANK[a.tier] - TIER_RANK[b.tier] || b.priority - a.priority || a.rule_id.localeCompare(b.rule_id));
  const checks: RuleCheck[] = [];
  const fired: Rule[] = [];
  const missingAll = new Set<string>();
  let missingTop: [number, number] | null = null;
  for (const rule of rules) {
    const missing = rule.requires.filter((p) => getVar(f, p) === undefined);
    const check: RuleCheck = { rule_id: rule.rule_id, clause_id: rule.clause_id, tier: rule.tier, priority: rule.priority, fired: false, missing, fields: fieldValues(rule, f), description: rule.description };
    if (missing.length) {
      missing.forEach((m) => missingAll.add(m));
      const level: [number, number] = [TIER_RANK[rule.tier], -rule.priority];
      if (!missingTop || level[0] < missingTop[0] || (level[0] === missingTop[0] && level[1] < missingTop[1])) missingTop = level;
    } else if (apply(rule.when, f)) {
      check.fired = true;
      fired.push(rule);
    }
    checks.push(check);
  }
  const base = { remedy: null, amount: null, fault: "indeterminate" as Fault, winning_rule_id: null, winning_clause_id: null, clause_ids: [] as string[], fired_rule_ids: fired.map((r) => r.rule_id), missing_facts: [...missingAll].sort(), note: null };
  if (!fired.length) {
    if (missingAll.size) return { checks, ruling: { ...base, decision: "needs_fact", explanation: "Required facts are missing: " + [...missingAll].sort().join(", ") } };
    return { checks, ruling: { ...base, decision: "escalate", explanation: "No compiled rule covers these facts; a person decides and the gap is logged for the policy owner." } };
  }
  const win = fired[0];
  const winLevel: [number, number] = [TIER_RANK[win.tier], -win.priority];
  if (missingTop && (missingTop[0] < winLevel[0] || (missingTop[0] === winLevel[0] && missingTop[1] < winLevel[1]))) {
    return { checks, ruling: { ...base, decision: "needs_fact", explanation: "Required facts are missing for a higher-precedence rule: " + [...missingAll].sort().join(", ") } };
  }
  const amount = amountOf(win, f);
  const clause = pack.clauses.find((c) => c.clause_id === win.clause_id);
  return {
    checks,
    ruling: {
      ...base,
      decision: win.effect.decision,
      remedy: win.effect.remedy,
      amount,
      fault: win.effect.fault,
      winning_rule_id: win.rule_id,
      winning_clause_id: win.clause_id,
      clause_ids: [...new Set(fired.map((r) => r.clause_id))],
      note: win.effect.note,
      explanation: `${win.description} (${win.rule_id}, ${win.tier} tier${clause ? `, "${clause.title}"` : ""}).`,
    },
  };
}

// ---------- PDP ----------

const REMEDY_ACTION: Record<string, string> = { refund: "refund", partial_refund: "partial_refund", replacement: "replacement", reship: "reship", store_credit: "store_credit", price_adjustment: "partial_refund", release_hold: "release_hold", cancel_order: "cancel_order", membership_refund: "refund", none: "reply", redeliver: "reship" };

export function authorize(ruling: Ruling, opts: RunOptions): ResolveRun["pdp"] {
  const money = ruling.decision === "approve" || ruling.decision === "partial";
  const action = money ? REMEDY_ACTION[ruling.remedy ?? "none"] ?? "reply" : ruling.decision === "escalate" ? "escalate" : "reply";
  const amt = money ? Number(ruling.amount ?? 0) : 0;
  const reasons: string[] = [];
  if (opts.mode === "shadow") return { decision: "recorded", action, amount: ruling.amount, limit: opts.auto_approve_limit, reasons: ["shadow mode: decision recorded, nothing executes"] };
  if (!money) return { decision: "allow", action, amount: null, limit: opts.auto_approve_limit, reasons: [`${action}: no money moves`] };
  if (ruling.winning_clause_id) reasons.push(`licensed by ${ruling.winning_clause_id}`);
  if (opts.mode === "assist") { reasons.push("assist mode: every action needs a person"); return { decision: "needs_approval", action, amount: ruling.amount, limit: opts.auto_approve_limit, reasons }; }
  if (amt > opts.auto_approve_limit) { reasons.push(`$${amt.toFixed(2)} exceeds the auto-approve limit $${opts.auto_approve_limit.toFixed(2)}`); return { decision: "needs_approval", action, amount: ruling.amount, limit: opts.auto_approve_limit, reasons }; }
  reasons.push(`$${amt.toFixed(2)} within the auto-approve limit $${opts.auto_approve_limit.toFixed(2)}`);
  return { decision: "allow", action, amount: ruling.amount, limit: opts.auto_approve_limit, reasons };
}

// ---------- the run ----------

const FAULT_TEXT: Record<Fault, string> = anonymiseDeep({
  customer: "the member",
  agent: "the member's AI agent (acted outside the member's instruction)",
  agent_platform: "the agent platform (attestation, protocol or mandate handling)",
  merchant: "BJ's (club, warehouse or systems)",
  third_party: "a third party (delivery partner, carrier or payment processor)",
  indeterminate: "not determinable from the facts on file",
});

function buildFacts(ticket: Ticket, order: Order | null, overrides: RunOptions["facts"]): FactBundle {
  const claim: Claim = { ...ticket.extracted, ...overrides };
  const itemId = overrides.item_id ?? ticket.extracted.item_id;
  const item: Item = order?.items.find((i) => i.item_id === itemId) ?? order?.items[0] ?? ({ item_id: "none", sku: "", name: "(no item)", department: "unknown", unit: "each", price: "0.00", quantity: 0, line_total: "0.00" } as Item);
  return { order: order as Order, item, claim, member: order?.member as Order["member"], fulfilment: order?.fulfilment as Order["fulfilment"] };
}

export function runCase(ticket: Ticket, ctx: { pack: RulePack; procedures: Procedure[]; orders: Order[]; categories: Category[] }, opts: RunOptions): ResolveRun {
  const order = ctx.orders.find((o) => o.order_id === ticket.order_id) ?? null;
  const category = ctx.categories.find((c) => c.key === ticket.category_key) ?? { key: ticket.category_key, name: ticket.category_key, description: "", lifecycle_note: "" };
  const facts = buildFacts(ticket, order, opts.facts);
  const { checks, ruling } = evaluate(ctx.pack, facts, ticket.category_key);
  const procedure = ctx.procedures.find((p) => p.category_key === ticket.category_key) ?? null;
  const pdp = authorize(ruling, opts);
  const case_id = b(`BJ-${sha256Hex(ticket.ticket_id).slice(0, 8).toUpperCase()}`);
  const hasPartner = !!order?.fulfilment.partner;
  const money = ruling.decision === "approve" || ruling.decision === "partial";
  const needsStaff = pdp.decision === "needs_approval";
  const rc = { channel: opts.channel, hasPartner, needsStaff, agentFault: ruling.fault === "agent" || ruling.fault === "agent_platform" };
  const names = [ticket.agent_message ? "" : (ticket.thread?.[0]?.text.match(/(?:Thanks|Regards|Best),?\s*\n?([A-Z][a-z]+ [A-Z][a-z]+)/)?.[1] ?? "")];

  // terms the customer confirms
  const terms: Record<string, unknown> = money
    ? { case_id, order_id: ticket.order_id, decision: ruling.decision, remedy: ruling.remedy, amount: ruling.amount, currency: "USD", fault: ruling.fault, clause_ids: ruling.clause_ids, rule_pack_version: ctx.pack.version, procedure: procedure ? `${procedure.category_key}@${procedure.version}` : null, settlement: ruling.note, valid_for_hours: 72 }
    : { case_id, order_id: ticket.order_id, decision: ruling.decision, remedy: null, amount: null, clause_ids: ruling.clause_ids, rule_pack_version: ctx.pack.version, explanation: ruling.explanation };
  const termsHash = sha256Hex(canonical(terms));
  const termsStatus: ResolveRun["terms"]["status"] = !money ? "n/a" : opts.customer_confirmed ? "confirmed" : "awaiting";
  const confirmation = termsStatus === "confirmed"
    ? opts.channel === "agent"
      ? { protocol: "umpyr-resolve/0.2", type: "terms_accepted", case_id, terms_hash: termsHash, accepted_by: "member via agent", agent: ticket.agent_message?.agent ?? null, at: "2026-10-27T14:03:11Z" }
      : { method: "email link", case_id, terms_hash: termsHash, accepted_at: "2026-10-27T14:03:11Z", ip_masked: "[ip]" }
    : null;

  // execution (merchant side, mock BJ's API)
  let execution: ResolveRun["execution"];
  if (!money) execution = { status: "none", request: null, response: null };
  else if (pdp.decision === "recorded") execution = { status: "shadow", request: null, response: null };
  else if (pdp.decision === "needs_approval" && !opts.staff_approved) execution = { status: "waiting_approval", request: null, response: null };
  else if (termsStatus !== "confirmed") execution = { status: "waiting_confirmation", request: null, response: null };
  else {
    const endpoint = pdp.action === "refund" || pdp.action === "partial_refund" ? `POST /v1/orders/${ticket.order_id}/refunds` : pdp.action === "reship" ? `POST /v1/orders/${ticket.order_id}/redeliveries` : pdp.action === "replacement" ? `POST /v1/orders/${ticket.order_id}/replacements` : pdp.action === "release_hold" ? `POST /v1/payments/holds/release` : pdp.action === "store_credit" ? `POST /v1/members/${order?.member.member_token}/credits` : pdp.action === "cancel_order" ? `POST /v1/orders/${ticket.order_id}/cancel` : `POST /v1/orders/${ticket.order_id}/actions`;
    const idem = sha256Hex(`${case_id}:${termsHash}`).slice(0, 24);
    const request = { endpoint, idempotency_key: idem, body: { order_id: ticket.order_id, member_token: order?.member.member_token, action: pdp.action, amount: ruling.amount, currency: "USD", reason_clause: ruling.winning_clause_id, terms_hash: termsHash, item_ids: facts.claim.item_ids ?? [facts.item.item_id] } };
    const response = anonymiseDeep({ status: "accepted", merchant_reference: `BJS-${sha256Hex(idem).slice(0, 10).toUpperCase()}`, settles_via: order?.payment_method === "bjs_card" ? "BJ's co-brand card credit" : "original card", expected_posting: "3-5 business days (illustrative)", executed_by: "BJ's order service (mock)" });
    execution = { status: "executed", request, response };
  }

  // audit record, hashed and signed
  const record = {
    case_id, ticket_id: ticket.ticket_id, channel: opts.channel, workspace: slugFor("bjs"), category: category.key, order_id: ticket.order_id,
    facts_hash: sha256Hex(canonical(facts)), rule_pack_version: ctx.pack.version, procedure: procedure ? `${procedure.category_key}@${procedure.version}` : null,
    checks: checks.map((c) => ({ rule_id: c.rule_id, fired: c.fired, missing: c.missing })),
    ruling: { decision: ruling.decision, remedy: ruling.remedy, amount: ruling.amount, fault: ruling.fault, winning_rule_id: ruling.winning_rule_id, clause_ids: ruling.clause_ids },
    pdp, terms_hash: termsHash, terms_status: termsStatus, execution: { status: execution.status, merchant_reference: (execution.response as { merchant_reference?: string } | null)?.merchant_reference ?? null },
    mode: opts.mode, pii_masked: true, is_synthetic: true,
  };
  const auditHash = sha256Hex(canonical(record));
  const signature = hmacSha256Hex(DEMO_SIGNING_KEY, auditHash);

  // reply
  const amountTxt = ruling.amount ? `$${ruling.amount}` : "";
  const reply = b(ruling.decision === "needs_fact"
    ? `Thanks for reaching out about order ${ticket.order_id}. To finish this we need one more detail: ${ruling.missing_facts.map(friendly).join(", ")}. Reply with that and we will complete the review right away.`
    : ruling.decision === "escalate"
      ? `Thanks for reaching out about order ${ticket.order_id}. This one needs a person: a BJ's specialist will review it and reply within one business day.`
      : money
        ? `Thanks for reaching out about order ${ticket.order_id}. ${ruling.note ?? ""} We will ${remedyVerb(ruling.remedy)}${amountTxt ? ` ${amountTxt}` : ""}. ${termsStatus === "confirmed" ? "You confirmed these terms, so it is now with BJ's for processing." : "Please confirm these terms so BJ's can process it."}`
        : `Thanks for reaching out about order ${ticket.order_id}. ${ruling.note ?? ruling.explanation} If any of the details above are wrong, reply and we will look again.`);
  const agent_reply = opts.channel === "agent"
    ? { protocol: "umpyr-resolve/0.2", case_id, status: needsStaff && !opts.staff_approved ? "queued_for_approval" : termsStatus === "awaiting" ? "awaiting_confirmation" : ruling.decision, decision: ruling.decision, remedy: ruling.remedy, amount: ruling.amount, currency: "USD", fault: ruling.fault, clause_ids: ruling.clause_ids, required_facts: ruling.missing_facts, terms_hash: money ? termsHash : null, confirm: money && termsStatus === "awaiting" ? { type: "terms_accepted", case_id, terms_hash: termsHash } : null, message: reply, audit_hash: auditHash }
    : null;

  const steps: RunStep[] = [];
  const push = (id: string, name: string, status: StepStatus, summary: string, detail?: string, data?: unknown) => steps.push({ id, n: steps.length + 1, name: b(name), status, raci: raciFor(id, rc), summary: b(summary), detail: detail === undefined ? detail : b(detail), data });
  push("intake", "Intake", "done", opts.channel === "agent" ? `Structured claim from ${ticket.agent_message?.agent.platform ?? "agent"} (${ticket.agent_message?.protocol}); PII masked on ingest.` : "Forwarded email parsed; PII masked on ingest (emails, phones, names, cards, addresses).", undefined, { masked: ticket.thread ? ticket.thread.map((m) => ({ ...m, text: maskText(m.text, names) })) : ticket.agent_message });
  push("order_match", "Order match", order ? "done" : "blocked", order ? `${order.order_id}: ${order.items.length} line${order.items.length === 1 ? "" : "s"}, $${order.total}, ${order.fulfilment.method.replace(/_/g, " ")}${order.fulfilment.partner ? ` via ${order.fulfilment.partner}` : ""}, placed by ${order.placed_by}${order.agent_id ? ` (${order.agent_id})` : ""}, member ${order.member.member_token} (${order.member.tier === "club_plus" ? "Club+" : "Club"}).` : `No order matched ${ticket.order_id}.`, undefined, order);
  push("classification", "Classification", "done", `${category.name} · router confidence ${(0.84 + (parseInt(sha256Hex(ticket.ticket_id).slice(0, 2), 16) % 14) / 100).toFixed(2)}.`, category.description);
  push("facts", "Fact extraction", "done", `${Object.keys(facts.claim).filter((k) => facts.claim[k as keyof Claim] !== undefined && facts.claim[k as keyof Claim] !== null).length} claim facts from the message; order, item, member and fulfilment facts from BJ's record. Edit any claim fact to re-run.`, undefined, facts);
  push("rule", "Compiled policy rule", ruling.winning_rule_id ? "fired" : ruling.decision === "needs_fact" ? "needs_fact" : "blocked", ruling.winning_rule_id ? `${ruling.winning_rule_id} fired (pack ${ctx.pack.version}); ${checks.filter((c) => c.fired).length} of ${checks.length} applicable rules fired.` : ruling.explanation, ruling.explanation, { checks, pack_version: ctx.pack.version });
  push("fault", "Fault decision", ruling.decision === "needs_fact" ? "waiting" : "done", `fault = ${ruling.fault}: ${FAULT_TEXT[ruling.fault]}.`);
  push("resolution", "Proposed resolution (procedure library)", ruling.decision === "needs_fact" ? "waiting" : "done", procedure ? `${procedure.name} @v${procedure.version} → ${ruling.decision}${ruling.remedy ? ` · ${ruling.remedy}` : ""}${amountTxt ? ` · ${amountTxt}` : ""}.` : "No approved procedure for this category.", ruling.note ?? undefined, procedure);
  push("pdp", "PDP authorization", pdp.decision === "allow" || pdp.decision === "recorded" ? "done" : pdp.decision === "needs_approval" ? (opts.staff_approved ? "done" : "waiting") : "blocked", `authorize(umpyr, ${pdp.action}, ${ticket.order_id}${pdp.amount ? `, $${pdp.amount}` : ""}) → ${pdp.decision}${pdp.decision === "needs_approval" && opts.staff_approved ? " → approved by BJ's staff" : ""}.`, pdp.reasons.join("; "), pdp);
  push("terms", "Customer confirmation of terms hash", termsStatus === "n/a" ? "skipped" : termsStatus === "confirmed" ? "done" : "waiting", termsStatus === "n/a" ? "No settlement to confirm; explanation sent with the cited clause." : termsStatus === "confirmed" ? `Member confirmed terms ${termsHash.slice(0, 16)}… ${opts.channel === "agent" ? "via a signed agent acknowledgement" : "via the email link"}.` : `Waiting for the member to confirm terms ${termsHash.slice(0, 16)}… (${opts.channel === "agent" ? "agent returns terms_accepted" : "email link"}). Nothing settles before this.`, undefined, { terms, hash: termsHash, confirmation });
  push("execute", "Execution on the merchant side (mock BJ's API)", execution.status === "executed" ? "done" : execution.status === "none" || execution.status === "shadow" ? "skipped" : "waiting", execution.status === "executed" ? `${(execution.request as { endpoint: string }).endpoint} → accepted, ref ${(execution.response as { merchant_reference: string }).merchant_reference}. Money never touches Umpyr.` : execution.status === "waiting_confirmation" ? "Blocked until the member confirms the terms hash." : execution.status === "waiting_approval" ? "Blocked until BJ's staff approve (above auto-approve limit or assist mode)." : execution.status === "shadow" ? "Shadow mode: nothing executes." : "No merchant action required.", undefined, execution);
  push("audit", "Signed audit record", "done", `sha256 ${auditHash.slice(0, 16)}… signed (HMAC-SHA256, kid ${DEMO_KID}).`, undefined, { record, hash: auditHash, signature, kid: DEMO_KID });
  const lifecycle = lifecycleNote(category, facts, ruling);
  push("close", "Lifecycle closure (BJ's side)", ruling.decision === "needs_fact" ? "waiting" : "done", lifecycle);

  return { case_id, ticket, order, category, facts, checks, ruling, procedure, pdp, terms: { terms, hash: termsHash, status: termsStatus, confirmation }, execution, audit: { record, hash: auditHash, signature, kid: DEMO_KID }, steps, reply, agent_reply, lifecycle_note: lifecycle, pack_version: ctx.pack.version };
}

export function friendly(path: string): string {
  const m: Record<string, string> = { "claim.has_photos": "a photo of the item", "claim.reported_within_hours": "when you noticed the problem", "claim.quantity_intended": "how many you meant to order", "claim.quantity_received": "how many you received", "claim.coupon_id": "which coupon you clipped", "claim.advertised_price": "the price you saw", "claim.used_benefits_since_renewal": "whether you have shopped since the renewal", "claim.wants_cancel_membership": "whether you want to cancel the membership", "claim.hold_age_hours": "when the pending charge appeared", "claim.cases_to_keep": "how many cases the member keeps", "claim.lines_affected": "how many lines were missing or substituted" };
  return m[path] ?? path;
}

function remedyVerb(remedy: string | null): string {
  switch (remedy) {
    case "refund": return "refund";
    case "partial_refund": return "refund the difference,";
    case "price_adjustment": return "adjust the price and refund";
    case "membership_fee_refund": case "membership_refund": return "refund your membership fee,";
    case "redeliver": return "redeliver the missing items at no charge";
    case "replacement": return "replace the item";
    case "release_hold": return "release the pending hold";
    case "store_credit": return "issue a credit of";
    default: return "process";
  }
}

function lifecycleNote(category: Category, facts: FactBundle, ruling: Ruling): string {
  const item = facts.item;
  const parts: string[] = [];
  if (category.lifecycle_note) parts.push(category.lifecycle_note);
  if (item.is_perishable && (facts.claim.reason === "spoiled" || facts.claim.reason === "damaged")) parts.push(`Disposition: ${item.name} marked as shrink (spoiled/damaged, no return); cold-chain check on the ${facts.fulfilment?.method === "same_day_delivery" ? "delivery partner's" : "club's"} handling logged.`);
  if (ruling.fault === "third_party" && facts.fulfilment?.partner) parts.push(`Recovery: cost recorded against ${facts.fulfilment.partner} under the fulfilment agreement (illustrative).`);
  if (ruling.fault === "agent" || ruling.fault === "agent_platform") parts.push("Signal fed back to Umpyr Check: this agent fingerprint's mandate-drift rate updates; the next order from it is scored with that history.");
  if (category.key === "membership_renewal") parts.push(ruling.remedy ? "Retention: membership status closed cleanly; win-back eligibility flagged for 90 days." : "Retention: member keeps benefits; renewal date confirmed in the reply.");
  return parts.join(" ");
}
