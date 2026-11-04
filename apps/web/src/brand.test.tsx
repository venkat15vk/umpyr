/**
 * Anonymous build guard (VITE_BRAND_MODE=anonymous, the investor site). Every page, every fixture run and every
 * engine response must come out without a real merchant name. This test is what keeps try.umpyr.org golden:
 * a future string that names Rockler, Alaska or BJ's fails here before it ships.
 */
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const ROUTES = ["/", "/retailer", "/airline", "/club", "/club/check", "/club/lifecycle"];

// Engine responses in the anonymous build are rewritten by api(); the stub below is what the live engine would say.
const SESSION = { id: "s1", workspace_id: "alaska", channel: "email", mode: "autonomous", auto_approve_limit: 100, status: "open", turn: 0, category_key: null };
const EXAMPLES = [{ category_key: "delay", text: "My Alaska Airlines flight AS 123 was delayed four hours", expected: { decision: "approve", remedy: "refund", amount: "1" } }];
const SUMMARY = { workspace: { id: "alaska", name: "Alaska Airlines", is_synthetic: true } };
function stubFetch() {
  vi.stubGlobal("fetch", vi.fn((url: string) => {
    const u = String(url);
    const body = u.includes("/examples") ? EXAMPLES : u.includes("/summary") ? SUMMARY : u.includes("/sessions") ? SESSION : u.includes("/health") ? { status: "ok", env: "test", db: true, llm_provider: "claude", model: "haiku", daily_cost_cap_usd: 1 } : u.includes("/admin/spend") ? { day: "2026-11-04", cap_usd: 1, spent_usd: 0.01, remaining_usd: 0.99, blocked: false, calls: 1, by_step: [] } : [];
    return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) });
  }));
}

describe("anonymous build (investor site)", () => {
  beforeEach(() => { vi.resetModules(); vi.stubEnv("VITE_BRAND_MODE", "anonymous"); stubFetch(); });
  afterEach(() => { cleanup(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

  it("rewrites names, ids, domains and flight numbers; leaves ids and Muse alone", async () => {
    const { b, FORBIDDEN, merchantName, pathFor, workspaceFor } = await import("./lib/brand");
    expect(b("Approve as BJ's staff")).toBe("Approve as Warehouse Club staff");
    expect(b("BJ's Wholesale Club is not live on Muse")).toBe("Warehouse Club is not live on Muse");
    expect(b("order BJ-7007, rule BJ-SDD-4, ref BJS-AB12, ticket BJ-T-LIVE-1")).toBe("order WC-7007, rule WC-SDD-4, ref WC-AB12, ticket WC-T-LIVE-1");
    expect(b("per the Alaska Airlines Contract of Carriage, flight AS 123 (AS1234)")).toBe("per the Major Airline Contract of Carriage, flight MA 123 (MA 1234)");
    expect(b("https://www.alaskaair.com/x and https://help.bjs.com/y and rockler.com")).toBe("https://majorairline.example/x and https://warehouseclub.example/y and specialtyretailer.example");
    expect(b("Rockler Woodworking and Hardware; Rockler's bench")).toBe("Specialty Retailer; Specialty Retailer's bench");
    expect(b("workspace alaska, payment bjs_card, Meta Muse")).toBe("workspace alaska, payment bjs_card, Meta Muse");
    expect(merchantName("rockler")).toBe("Specialty Retailer");
    expect(pathFor("bjs", "/check")).toBe("/club/check");
    expect(workspaceFor("airline")).toBe("alaska");
    expect(workspaceFor("alaska")).toBe("alaska");
    expect(FORBIDDEN.test("Warehouse Club · Specialty Retailer · Major Airline · WC-7007")).toBe(false);
  });

  it("api() rewrites every string in an engine response", async () => {
    const { api } = await import("./lib/api");
    const r = await api<typeof SUMMARY>("/workspaces/alaska/summary");
    expect(r.workspace.name).toBe("Major Airline");
    expect(r.workspace.id).toBe("alaska");
  });

  it("every fixture run (tickets, agent cases, Check scenarios, merchant chat) is free of real names", async () => {
    const { FORBIDDEN } = await import("./lib/brand");
    const fx = await import("./lib/bjs/fixtures");
    const { runCase } = await import("./lib/bjs/resolveEngine");
    const { negotiate } = await import("./lib/bjs/negotiation");
    const { decide } = await import("./lib/bjs/checkEngine");
    const { customerTurn, emptyState, merchantAnswer, toOps } = await import("./lib/bjs/chat");
    const ctx = { pack: fx.pack, procedures: fx.procedures, orders: fx.orders, categories: fx.categories, history: fx.history };
    const nctx = { ...ctx, scenarios: fx.scenarios };
    const outputs: unknown[] = [fx];
    for (const t of fx.tickets) {
      for (const staff of [false, true]) {
        const run = runCase(t, ctx, { channel: t.channel, mode: "autonomous", auto_approve_limit: 100, facts: {}, customer_confirmed: staff, staff_approved: staff });
        outputs.push(run, toOps(run, ctx, { turn: 1, prev: null, factsChanged: [], messageCategory: null, pushback: false, classification: null }));
      }
    }
    for (const c of fx.agentCases) for (const limit of [100, 5000]) for (const by of [null, "member", "agent_platform"] as const) outputs.push(negotiate(c, nctx, { auto_approve_limit: limit, mode: "autonomous", staff_approved: true, confirmation_by: by, adapter: "pap-mcp" }));
    for (const s of fx.scenarios) outputs.push(decide(s.request, 1, "0".repeat(64)));
    let r = customerTurn(emptyState(), `The strawberries from order ${fx.orders[1].order_id} were crushed when I picked them up today. I'd like a refund.`, { channel: "email", mode: "autonomous", auto_approve_limit: 100 }, ctx);
    outputs.push(r);
    r = customerTurn(r.state, "Yes, I have a photo attached.", { channel: "email", mode: "autonomous", auto_approve_limit: 100 }, ctx);
    outputs.push(r, customerTurn(emptyState(), "hello", { channel: "email", mode: "autonomous", auto_approve_limit: 100 }, ctx));
    for (const q of ["what drifts most?", "top categories by pain", "which rule fires most?", "how many cases come from agents?", "which clauses are illustrative?", "anything else"]) outputs.push(merchantAnswer(q, ctx, fx.tickets));
    const text = JSON.stringify(outputs);
    const m = text.match(new RegExp(`.{0,60}(${FORBIDDEN.source}).{0,60}`));
    expect(m, m ? `found ${m[1]} in: ${m[0]}` : "").toBeNull();
    expect(text.length).toBeGreaterThan(100000);
  });

  it("every page renders without a real merchant name, link or title", async () => {
    const { FORBIDDEN } = await import("./lib/brand");
    const { default: App } = await import("./App");
    for (const route of ROUTES) {
      window.history.pushState({}, "", route);
      const { unmount } = render(<App />);
      await act(async () => { await new Promise((r) => setTimeout(r, 30)); });
      await waitFor(() => expect(document.body.textContent?.length ?? 0).toBeGreaterThan(200));
      const html = document.body.innerHTML;
      const m = html.match(new RegExp(`.{0,80}(${FORBIDDEN.source}).{0,80}`));
      expect(m, `${route}: found ${m?.[1]} in: ${m?.[0]}`).toBeNull();
      expect(html).not.toMatch(/href="https?:\/\/[^"]*(alaskaair|bjs\.com|rockler\.com)/);
      unmount();
      cleanup();
    }
  });

  it("old named links land on the stand-in routes", async () => {
    const { default: App } = await import("./App");
    for (const [from, to] of [["/alaska", "/airline"], ["/rockler", "/retailer"], ["/bjs/check?scenario=x", "/club/check?scenario=x"], ["/bjs", "/club"]]) {
      window.history.pushState({}, "", from);
      const { unmount } = render(<App />);
      await waitFor(() => expect(window.location.pathname + window.location.search).toBe(to));
      unmount();
      cleanup();
    }
  });
});

describe("named build (demo.umpyr.org) is unchanged", () => {
  it("keeps the merchant names and routes", async () => {
    vi.resetModules();
    const { b, merchantName, pathFor, workspaceFor, ANON } = await import("./lib/brand");
    expect(ANON).toBe(false);
    expect(b("Alaska Airlines BJ-7007")).toBe("Alaska Airlines BJ-7007");
    expect(merchantName("bjs")).toBe("BJ's");
    expect(pathFor("rockler")).toBe("/rockler");
    expect(workspaceFor("rockler")).toBe("rockler");
  });
});
