import { describe, expect, it } from "vitest";
import casesJson from "../../../../../samples/bjs/agent_cases.json";
import categoriesJson from "../../../../../samples/bjs/categories.json";
import scenariosJson from "../../../../../samples/bjs/check_scenarios.json";
import historyJson from "../../../../../samples/bjs/history.json";
import ordersJson from "../../../../../samples/bjs/orders.json";
import packJson from "../../../../../samples/bjs/rule_pack.json";
import proceduresJson from "../../../../../samples/bjs/procedures.json";
import { decide } from "./checkEngine";
import { checkEvidenceFor, type CheckScenario } from "./checkLink";
import { negotiate, type AgentCasesFile, type NegotiationCtx, type NegotiationOptions } from "./negotiation";
import { canonical, sha256Hex } from "./sha256";
import type { Order } from "./types";

const scenarios = (scenariosJson as { scenarios: CheckScenario[] }).scenarios;
const ctx = { pack: packJson, procedures: proceduresJson, orders: ordersJson, categories: categoriesJson, scenarios, history: historyJson } as unknown as NegotiationCtx;
const cases = (casesJson as unknown as AgentCasesFile).cases;
const byId = (id: string) => cases.find((c) => c.case_id === id)!;
const base: NegotiationOptions = { auto_approve_limit: 100, mode: "autonomous", staff_approved: false, confirmation_by: null, adapter: "pap-mcp" };

describe("Check link: the record Resolve pulls is the record Check committed", () => {
  it("every agent-placed order resolves to a real Check decision whose static fields match the fixture", () => {
    for (const o of ctx.orders.filter((x) => x.check)) {
      const ev = checkEvidenceFor(o, scenarios)!;
      expect(ev, o.order_id).not.toBeNull();
      expect(ev.decision.decision, o.order_id).toBe(o.check!.decision);
      expect(ev.decision.intent.outcome, o.order_id).toBe(o.check!.intent_outcome);
      expect(ev.decision.commitment.hash).toBe(sha256Hex(canonical(ev.decision.commitment.committed)));
      expect(ev.decision.decision_id).toBe(`chk_${ev.decision.commitment.hash.slice(0, 12)}`);
    }
  });
  it("orders whose request is a Check scenario verbatim get the same decision id as the Check page", () => {
    for (const s of scenarios.filter((x) => x.resolve_order_id)) {
      const o = ctx.orders.find((x) => x.order_id === s.resolve_order_id)!;
      if (o.check!.request_id !== s.request.request_id) continue; // BJ-7012 is a variant of the pack-size scenario
      expect(checkEvidenceFor(o, scenarios)!.decision.decision_id).toBe(decide(s.request).decision_id);
    }
  });
});

describe("agent-to-agent negotiation", () => {
  it("BJ-A-1 converges over several turns: evidence changes a ruling, a counter-argument wins a licensed concession, agreement, execution", () => {
    const n = negotiate(byId("BJ-A-1"), ctx, base);
    const kinds = n.turns.map((t) => `${t.speaker}:${t.kind}`);
    expect(kinds).toEqual(["muse:claim", "umpyr:ask_fact", "muse:provide_fact", "umpyr:offer", "muse:counter", "umpyr:offer", "muse:accept", "umpyr:terms", "muse:terms_accepted", "bjs:execute", "umpyr:audit"]);
    expect(n.turns[3].position).toBe("changed_on_evidence");
    expect(n.turns[5].position).toBe("conceded");
    expect(n.turns[5].offer?.total).toBe("63.75");
    expect(n.final.termination?.kind).toBe("agreement");
    expect(n.terms?.status).toBe("confirmed");
    expect(n.execution.status).toBe("executed");
    expect(n.fault.headline).toBe("third_party");
    expect(n.turns[1].evidence.map((e) => e.kind)).toEqual(expect.arrayContaining(["mandate", "check_record", "order", "policy_rule"]));
    expect(n.audit.hash).toBe(sha256Hex(canonical(n.audit.record)));
  });
  it("BJ-A-2 holds firm on the Check evidence: member-signed step-up, final sale, no concession, ruling accepted", () => {
    const n = negotiate(byId("BJ-A-2"), ctx, base);
    expect(n.turns.map((t) => t.kind)).toEqual(["claim", "deny", "pushback", "deny", "acknowledge", "audit"]);
    expect(n.turns[3].position).toBe("held");
    expect(n.turns[1].text).toMatch(/signed checkout mandate/);
    expect(n.runs.plates.ruling.winning_rule_id).toBe("BJ-RET-5.r2");
    expect(n.fault.headline).toBe("customer");
    expect(n.final.termination?.kind).toBe("ruling_accepted");
    expect(n.execution.status).toBe("none");
  });
  it("BJ-A-2 what-if: the same step-up answered by the platform flips fault to the agent and the excess is refunded", () => {
    const n = negotiate(byId("BJ-A-2"), ctx, { ...base, staff_approved: true, confirmation_by: "agent_platform" });
    expect(n.check?.what_if).toBe(true);
    expect(n.runs.plates.ruling.winning_rule_id).toBe("BJ-AGT-1.r1");
    expect(n.fault.headline).toBe("agent");
    expect(n.final.termination?.kind).toBe("agreement");
    expect(n.turns.some((t) => t.kind === "staff_approval")).toBe(true);
  });
  it("BJ-A-3 refers a choice to the member through the agent, then stops at the auto-approve ceiling until a person approves", () => {
    const n = negotiate(byId("BJ-A-3"), ctx, base);
    expect(n.turns.map((t) => t.kind)).toEqual(["claim", "ask_fact", "refer_to_member", "member_decision", "needs_approval", "acknowledge", "audit"]);
    expect(n.final.customer_referral?.resumed).toBe(true);
    expect(n.final.termination?.kind).toBe("escalate_staff");
    expect(n.final.policy_limits.ceiling_hit).toBe(true);
    expect(n.final.current_offer?.total).toBe("970.92");
    const approved = negotiate(byId("BJ-A-3"), ctx, { ...base, staff_approved: true });
    expect(approved.final.termination?.kind).toBe("agreement");
    expect(approved.execution.status).toBe("executed");
    const raised = negotiate(byId("BJ-A-3"), ctx, { ...base, auto_approve_limit: 1500 });
    expect(raised.turns.some((t) => t.kind === "needs_approval")).toBe(false);
    expect(raised.final.termination?.kind).toBe("agreement");
  });
  it("BJ-A-4 has no clause to rule on and escalates to a person with the Check evidence attached", () => {
    const n = negotiate(byId("BJ-A-4"), ctx, base);
    expect(n.turns.map((t) => t.kind)).toEqual(["claim", "escalate", "acknowledge", "audit"]);
    expect(n.final.termination?.kind).toBe("escalate_staff");
    expect(n.final.termination?.reason).toMatch(/policy gap/);
    expect(n.turns[1].evidence.some((e) => e.kind === "check_record")).toBe(true);
    expect(n.fault.headline).toBe("indeterminate");
  });
  it("shadow mode records the agreement and executes nothing", () => {
    const n = negotiate(byId("BJ-A-1"), ctx, { ...base, mode: "shadow" });
    expect(n.final.termination?.kind).toBe("agreement");
    expect(n.execution.status).toBe("shadow");
  });
  it("every turn carries a RACI and the audit record lists every turn", () => {
    for (const c of cases) {
      const n = negotiate(c, ctx, base);
      for (const t of n.turns) expect(Object.keys(t.raci).length, `${c.case_id} turn ${t.n}`).toBeGreaterThan(0);
      expect((n.audit.record.turns as unknown[]).length).toBe(n.turns.length - 1);
    }
  });
});

describe("Check additions", () => {
  it("declines an expired membership with the remedy in the reply", () => {
    const s = scenarios.find((x) => x.id === "membership_expired")!;
    const d = decide(s.request);
    expect(d.decision).toBe("decline");
    expect(d.reason_codes).toContain("MEMBERSHIP_EXPIRED");
    expect(d.actions[0].message_to_agent.remedy).toBe("renew_membership");
  });
  it("declines a duplicate agent retry and returns the original decision id", () => {
    const s = scenarios.find((x) => x.id === "duplicate_agent_retry")!;
    const first = scenarios.find((x) => x.id === "clean_order")!;
    const d = decide(s.request);
    expect(d.decision).toBe("decline");
    expect(d.duplicate_of).toBe(decide(first.request).decision_id);
    expect(d.actions[0].message_to_agent.type).toBe("duplicate");
  });
  it("the orders fixture is consistent: new Muse orders are agent-placed and carry a Check link", () => {
    for (const id of ["BJ-7011", "BJ-7012", "BJ-7013", "BJ-7007"]) {
      const o = (ordersJson as unknown as Order[]).find((x) => x.order_id === id)!;
      expect(o.placed_by, id).toBe("agent");
      expect(o.check?.scenario_id, id).toBeTruthy();
    }
  });
});
