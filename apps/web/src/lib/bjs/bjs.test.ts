import { describe, expect, it } from "vitest";
import scenariosJson from "../../../../../samples/bjs/check_scenarios.json";
import categories from "../../../../../samples/bjs/categories.json";
import orders from "../../../../../samples/bjs/orders.json";
import pack from "../../../../../samples/bjs/rule_pack.json";
import procedures from "../../../../../samples/bjs/procedures.json";
import tickets from "../../../../../samples/bjs/tickets.json";
import { decide } from "./checkEngine";
import { apply } from "./jsonlogic";
import { runCase, type RunOptions } from "./resolveEngine";
import { canonical, hmacSha256Hex, sha256Hex } from "./sha256";
import type { Category, CheckRequest, Order, Procedure, RulePack, Ticket } from "./types";

const ctx = { pack: pack as unknown as RulePack, procedures: procedures as unknown as Procedure[], orders: orders as unknown as Order[], categories: categories as Category[] };
const base: RunOptions = { channel: "email", mode: "autonomous", auto_approve_limit: 100, facts: {}, customer_confirmed: false, staff_approved: false };

describe("sha256", () => {
  it("matches FIPS 180-4 vectors", () => {
    expect(sha256Hex("")).toBe("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    expect(sha256Hex("abc")).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    expect(sha256Hex("a".repeat(1000))).toBe("41edece42d63e8d9bf515a9ba6932e1c20cbc9f5a5d134645adb5db1b9737ea3");
  });
  it("HMAC-SHA256 RFC 4231 case 2", () => {
    expect(hmacSha256Hex("Jefe", "what do ya want for nothing?")).toBe("5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843");
  });
  it("canonical JSON sorts keys and drops undefined", () => {
    expect(canonical({ b: 1, a: [{ d: undefined, c: "x" }] })).toBe('{"a":[{"c":"x"}],"b":1}');
  });
});

describe("json logic", () => {
  it("evaluates the operators the pack uses", () => {
    const f = { item: { price: "10.50", qty: 3, win: null }, claim: { h: 40 } };
    expect(apply({ "<=": [{ var: "claim.h" }, { "*": [{ var: "item.qty" }, 24] }] }, f)).toBe(true);
    expect(apply({ "==": [{ var: "item.win" }, null] }, f)).toBe(true);
    expect(apply({ "!=": [{ var: "item.win" }, null] }, f)).toBe(false);
    expect(apply({ in: ["TV", "LG 65 TV"] }, f)).toBe(true);
    expect(apply({ "-": [{ var: "item.price" }, 0.5] }, f)).toBe(10);
  });
});

describe("Check scenarios", () => {
  const scenarios = (scenariosJson as { scenarios: { id: string; expected: string; ordinary_expected: string; request: CheckRequest }[] }).scenarios;
  it("has 6 scenarios", () => expect(scenarios.length).toBe(6));
  for (const s of scenarios) {
    it(`${s.id}: umpyr=${s.expected} ordinary=${s.ordinary_expected}`, () => {
      const d = decide(s.request, 1, "0".repeat(64));
      expect(d.decision).toBe(s.expected);
      expect(d.comparison.ordinary_fraud_tool.decision).toBe(s.ordinary_expected);
      expect(d.commitment.hash).toBe(sha256Hex(canonical(d.commitment.committed)));
      expect(d.steps.length).toBe(9);
      expect(d.latency_ms).toBe(d.steps.reduce((a, x) => a + x.ms, 0));
    });
  }
  it("flips pack-size drift to approve when the cart unit is corrected", () => {
    const s = scenarios.find((x) => x.id === "pack_size_vs_quantity")!;
    const req = JSON.parse(JSON.stringify(s.request)) as CheckRequest;
    req.cart.lines[0] = { ...req.cart.lines[0], unit: "pack", unit_price: "8.99", line_total: "89.90", sku: "WF-PLT-12" };
    req.cart.total = (89.9 + 11.99 + 9.99).toFixed(2);
    expect(decide(req).decision).toBe("approve");
  });
  it("flips clean to decline when the signature fails", () => {
    const s = scenarios.find((x) => x.id === "clean_order")!;
    const req = JSON.parse(JSON.stringify(s.request)) as CheckRequest;
    req.mandate.signature.valid = false;
    expect(decide(req).decision).toBe("decline");
  });
});

describe("Resolve tickets", () => {
  const all = tickets as unknown as Ticket[];
  it("covers 8 categories on both channels", () => {
    const keys = new Set(all.map((t) => `${t.category_key}:${t.channel}`));
    expect(keys.size).toBe(16);
  });
  const expected: Record<string, string> = {
    "BJ-T-1001": "approve", "BJ-T-1002": "approve", "BJ-T-2001": "approve", "BJ-T-2002": "needs_fact", "BJ-T-3001": "approve", "BJ-T-3002": "deny",
    "BJ-T-4001": "approve", "BJ-T-4002": "escalate", "BJ-T-5001": "approve", "BJ-T-5002": "deny", "BJ-T-6001": "deny", "BJ-T-6002": "approve",
    "BJ-T-7001": "approve", "BJ-T-7002": "approve", "BJ-T-8001": "approve", "BJ-T-8002": "deny",
  };
  for (const t of all) {
    it(`${t.ticket_id} (${t.category_key}, ${t.channel}) -> ${expected[t.ticket_id]}`, () => {
      const r = runCase(t, ctx, { ...base, channel: t.channel });
      expect(r.ruling.decision).toBe(expected[t.ticket_id]);
      expect(r.steps.length).toBe(12);
      expect(r.pack_version).toBe("2026-11-01.1");
      if (r.ruling.decision === "approve") expect(r.ruling.winning_rule_id).toBeTruthy();
      expect(r.audit.hash).toBe(sha256Hex(canonical(r.audit.record)));
      for (const s of r.steps) expect(Object.keys(s.raci).length).toBeGreaterThan(0);
    });
  }
  it("settles only after confirmation; large refunds need staff approval", () => {
    const t = all.find((x) => x.ticket_id === "BJ-T-7001")!;
    const r1 = runCase(t, ctx, base);
    expect(r1.ruling.amount).toBe("970.92");
    expect(r1.pdp.decision).toBe("needs_approval");
    expect(r1.execution.status).toBe("waiting_approval");
    const r2 = runCase(t, ctx, { ...base, staff_approved: true });
    expect(r2.execution.status).toBe("waiting_confirmation");
    const r3 = runCase(t, ctx, { ...base, staff_approved: true, customer_confirmed: true });
    expect(r3.execution.status).toBe("executed");
    expect(r3.ruling.fault).toBe("agent");
  });
  it("editable fact flips the late perishable claim", () => {
    const t = all.find((x) => x.ticket_id === "BJ-T-2002")!;
    expect(runCase(t, ctx, { ...base, facts: { has_photos: true } }).ruling.decision).toBe("approve");
    expect(runCase(t, ctx, { ...base, facts: { has_photos: false } }).ruling.decision).toBe("deny");
  });
  it("masks PII on intake", () => {
    const t = all.find((x) => x.ticket_id === "BJ-T-1001")!;
    const r = runCase(t, ctx, base);
    const masked = JSON.stringify((r.steps[0].data as { masked: unknown }).masked);
    expect(masked).not.toContain("@example");
    expect(masked).not.toContain("555-0142");
  });
});
