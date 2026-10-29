import { describe, expect, it } from "vitest";
import categoriesJson from "../../../../../samples/bjs/categories.json";
import historyJson from "../../../../../samples/bjs/history.json";
import ordersJson from "../../../../../samples/bjs/orders.json";
import packJson from "../../../../../samples/bjs/rule_pack.json";
import proceduresJson from "../../../../../samples/bjs/procedures.json";
import ticketsJson from "../../../../../samples/bjs/tickets.json";
import { classify, customerTurn, emptyState, extractFacts, findOrderId, merchantAnswer, rerun, ticketFromText, toOps, type Ctx } from "./chat";
import { runCase } from "./resolveEngine";
import type { Category, Order, Procedure, RulePack, Ticket } from "./types";

const ctx: Ctx = { pack: packJson as unknown as RulePack, procedures: proceduresJson as unknown as Procedure[], orders: ordersJson as unknown as Order[], categories: categoriesJson as Category[], history: historyJson as unknown as Ctx["history"] };
const tickets = ticketsJson as unknown as Ticket[];
const opts = { channel: "email" as const, mode: "autonomous" as const, auto_approve_limit: 100 };

describe("router and extractor", () => {
  it("finds order ids in free text", () => {
    expect(findOrderId("my order bj-7002 was spoiled")).toBe("BJ-7002");
    expect(findOrderId("no number here")).toBeNull();
  });
  it("classifies each category from plain words", () => {
    const cases: [string, string][] = [
      ["The Tide PODS were missing from my same-day delivery", "sdd_missing_substituted"],
      ["The salmon I picked up smelled spoiled, photo attached", "perishable_damaged"],
      ["Curbside order wasn't ready and I waited 40 minutes", "pickup_issue"],
      ["My clipped coupon was not applied at ExpressPay", "coupon_price_adjust"],
      ["You auto-renewed my membership, please cancel and refund", "membership_renewal"],
      ["I want to return the TV I bought, change of mind", "return_eligibility"],
      ["My AI agent ordered 10 cases of plates instead of 10 packs", "agent_order_error"],
      ["I was charged twice for the same order", "duplicate_charge_hold"],
    ];
    for (const [text, cat] of cases) expect(classify(text, null).category_key, text).toBe(cat);
  });
  it("falls back to the order record when words are vague", () => {
    const order = ctx.orders.find((o) => o.order_id === "BJ-7009")!;
    const c = classify("something is wrong with BJ-7009", order);
    expect(c.category_key).toBe("duplicate_charge_hold");
    expect(c.source).toBe("fallback");
    expect(classify("hello", null).category_key).toBe("unclassified");
  });
  it("extracts only what the text supports", () => {
    const order = ctx.orders.find((o) => o.order_id === "BJ-7002")!;
    const f = extractFacts("The Driscoll's strawberries were crushed, I have a photo, noticed it 3 hours ago, refund please", "perishable_damaged", order);
    expect(f.item_id).toBe("L2");
    expect(f.has_photos).toBe(true);
    expect(f.reported_within_hours).toBe(3);
    expect(f.requested_remedy).toBe("refund");
    const g = extractFacts("strawberries crushed", "perishable_damaged", order);
    expect(g.has_photos).toBeUndefined();
    expect(g.reported_within_hours).toBeUndefined();
  });
  it("builds a ticket with the agent envelope on the agent channel", () => {
    const { ticket } = ticketFromText("Charged twice for BJ-7008", "agent", ctx);
    expect(ticket.agent_message?.protocol).toBe("umpyr-resolve/0.2");
    expect(ticket.category_key).toBe("duplicate_charge_hold");
    expect(ticket.extracted.reason).toBe("duplicate_charge");
  });
});

describe("ops mapping", () => {
  it("maps a run onto the Rockler/Alaska Ops shape with all six sections populated", () => {
    const t = tickets.find((x) => x.ticket_id === "BJ-T-1001")!;
    const run = runCase(t, ctx, { ...opts, facts: {}, customer_confirmed: false, staff_approved: false });
    const ops = toOps(run, ctx, { turn: 1, prev: null, factsChanged: [], messageCategory: null, pushback: false, classification: null });
    expect(ops.classification.category_key).toBe("sdd_missing_substituted");
    expect(ops.neighbours.length).toBe(5);
    expect(ops.neighbours[0].category_key).toBe("sdd_missing_substituted");
    expect(ops.plan?.live.steps.length).toBeGreaterThan(3);
    expect(ops.procedure.steps.length).toBeGreaterThan(3);
    expect(ops.ruling.decision).toBe("approve");
    expect(ops.ruling.citations?.[0].url).toMatch(/^https?:/);
    expect(ops.authorization?.decision).toBe("allow");
    expect(ops.governance.audit_hash).toBe(run.audit.hash);
    expect(ops.status).toBe("awaiting_confirmation");
    expect(ops.case?.llm_calls).toBe(0);
  });
  it("marks a denial as denied and an escalation as escalated", () => {
    const deny = runCase(tickets.find((x) => x.ticket_id === "BJ-T-3002")!, ctx, { ...opts, facts: {}, customer_confirmed: false, staff_approved: false });
    expect(toOps(deny, ctx, { turn: 1, prev: null, factsChanged: [], messageCategory: null, pushback: false, classification: null }).status).toBe("denied");
    const esc = runCase(tickets.find((x) => x.ticket_id === "BJ-T-4002")!, ctx, { ...opts, facts: {}, customer_confirmed: false, staff_approved: false });
    expect(toOps(esc, ctx, { turn: 1, prev: null, factsChanged: [], messageCategory: null, pushback: false, classification: null }).ruling.escalation?.type).toBe("policy_gap");
  });
});

describe("customer turns", () => {
  it("asks for the order number when none is given", () => {
    const r = customerTurn(emptyState(), "my strawberries were crushed", opts, ctx);
    expect(r.ops.record.found).toBe(false);
    expect(r.reply).toMatch(/order number/);
    expect(r.ops.status).toBe("needs_fact");
  });
  it("runs a free-text case end to end: facts, confirmation, execution", () => {
    let r = customerTurn(emptyState(), "The strawberries from order BJ-7002 were crushed when I picked them up today. I'd like a refund.", opts, ctx);
    expect(r.ops.classification.category_key).toBe("perishable_damaged");
    expect(r.ops.status).toBe("needs_fact"); // no photo yet
    expect(r.reply).toMatch(/photo/);
    r = customerTurn(r.state, "Yes, I have a photo attached.", opts, ctx);
    expect(r.ops.turn_delta?.facts_changed).toContain("has_photos");
    expect(r.ops.turn_delta?.ruling_changed).toBe(true);
    expect(r.ops.status).toBe("awaiting_confirmation");
    r = customerTurn(r.state, "I confirm these terms.", opts, ctx);
    expect(r.ops.status).toBe("resolved");
    expect(r.run?.execution.status).toBe("executed");
    expect(r.ops.turn).toBe(3);
  });
  it("picks up the order number on a later turn", () => {
    let r = customerTurn(emptyState(), "I was charged twice for my delivery", opts, ctx);
    expect(r.ops.record.found).toBe(false);
    r = customerTurn(r.state, "The order is BJ-7008", opts, ctx);
    expect(r.ops.record.found).toBe(true);
    expect(r.ops.ruling.decision).toBe("approve");
  });
  it("staff approval and fact edits re-run the same turn", () => {
    const t = tickets.find((x) => x.ticket_id === "BJ-T-7001")!;
    let r = customerTurn(emptyState(), t.thread![0].text, opts, ctx, t);
    expect(r.ops.status).toBe("pending_approval");
    r = rerun({ ...r.state, staff_approved: true }, opts, ctx)!;
    expect(r.ops.status).toBe("awaiting_confirmation");
    expect(r.ops.turn).toBe(1);
    const edited = rerun({ ...r.state, overrides: { cases_to_keep: 10 } }, opts, ctx)!;
    expect(edited.ops.ruling.amount).not.toBe(r.ops.ruling.amount);
  });
  it("pushback on a denial surfaces precedent from history", () => {
    const t = tickets.find((x) => x.ticket_id === "BJ-T-6001")!;
    let r = customerTurn(emptyState(), t.thread![0].text, opts, ctx, t);
    expect(r.ops.status).toBe("denied");
    r = customerTurn(r.state, "That is unfair, I insist on speaking to a manager.", opts, ctx);
    expect(r.ops.turn_delta?.pushback).toBe(true);
    expect(r.ops.turn_delta?.precedent?.exists).toBe(true);
    expect(r.reply).toMatch(/specialist/);
  });
  it("every fixture ticket produces an ops view with neighbours and a procedure", () => {
    for (const t of tickets) {
      const r = customerTurn(emptyState(), t.thread?.[0]?.text ?? JSON.stringify(t.agent_message), { ...opts, channel: t.channel }, ctx, t);
      expect(r.ops.neighbours.length, t.ticket_id).toBe(5);
      expect(r.ops.procedure.ref, t.ticket_id).not.toBeNull();
      expect(r.ops.governance.audit_hash, t.ticket_id).toHaveLength(64);
    }
  });
});

describe("merchant chat", () => {
  it("answers drift, category, rule and clause questions from the fixtures", () => {
    expect(merchantAnswer("what drifts most?", ctx, tickets)).toMatch(/drift from policy/);
    expect(merchantAnswer("top categories by pain", ctx, tickets)).toMatch(/Closed cases by category/);
    expect(merchantAnswer("which rule fires most?", ctx, tickets)).toMatch(/BJ-/);
    expect(merchantAnswer("which clauses are illustrative?", ctx, tickets)).toMatch(/illustrative/);
    expect(merchantAnswer("hello", ctx, tickets)).toMatch(/I can answer/);
  });
});
