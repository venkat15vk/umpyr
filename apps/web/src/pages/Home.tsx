import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, PUBLIC_DEMO, type Health, type Spend, type Workspace } from "../lib/api";
import { ANON, b, BJS_BASE, merchantName, pathFor } from "../lib/brand";

/** Demo hub. Rockler and Alaska run live; the three BJ's cards tell one story: a Muse order before payment, after the order, and end to end. */
const DEMOS: { to: string; title: string; product: string; line: string; live: boolean }[] = [
  { to: pathFor("rockler"), title: merchantName("rockler"), product: "Resolve", line: "Woodworking retailer. Live chat ruled by compiled return, guarantee and backorder policy; ops view shows every step.", live: true },
  { to: pathFor("alaska"), title: merchantName("alaska"), product: "Resolve", line: "Refunds, delays, bag guarantee and DOT rules in precedence order; options turns when the passenger must choose.", live: true },
  { to: `${BJS_BASE}/check`, title: b("BJ's: Muse order lifecycle, Part 1, Check (before payment)"), product: "Check", line: "A Muse order arrives over the protocol adapter: intent mandate vs cart, risk score (table stakes), intent verdict, approve / step-up / decline, and a hash-committed decision record kept for Part 2.", live: false },
  { to: BJS_BASE, title: b("BJ's: Muse order lifecycle, Part 2, Resolve (after the order)"), product: "Resolve", line: b("A Muse complaint arrives; Umpyr's agent pulls the Check record and mandate as evidence and negotiates with the member's agent turn by turn: offers, holds, concessions, fault, terms hash, BJ's executes, signed audit."), live: false },
  { to: `${BJS_BASE}/lifecycle`, title: b("BJ's: End-to-end Muse order, agent-to-agent"), product: "Lifecycle", line: b("One order on one timeline: ask Muse, adapter, Check, payment by BJ's processor, delivery outside Umpyr, question, dispute, negotiation, execution, audit. RACI per step."), live: false },
];

export function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [spend, setSpend] = useState<Spend | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Health>("/health").then(setHealth).catch((e) => setError(String(e)));
    api<Spend>("/admin/spend").then(setSpend).catch(() => {});
    api<Workspace[]>("/workspaces").then(setWorkspaces).catch(() => {});
  }, []);

  return (
    <div className="grid gap-4 md:grid-cols-3">
      <section className="panel p-4 md:col-span-2">
        <h1 className="text-xl font-semibold">No help desk required.</h1>
        <p className="muted mt-1">
          Umpyr resolves customer disputes with an approved procedure, compiled policy clauses, and deterministic rules (Resolve), and scores risk and intent on
          orders placed by personal AI agents before payment (Check). Pick a demo.
        </p>
        <ul className="mt-4 grid gap-3 sm:grid-cols-2">
          {DEMOS.map((d) => (
            <li key={d.to} className="panel p-3">
              <Link to={d.to} className="flex flex-wrap items-center gap-2 font-medium">
                <span>{d.title}</span>
                <span className="badge">{d.product}</span>
                {!d.live && <span className="badge badge-amber">fixtures</span>}
              </Link>
              <p className="muted mt-1 text-xs">{d.line}</p>
              <Link className="btn btn-primary mt-2 text-xs" to={d.to}>Open demo</Link>
            </li>
          ))}
        </ul>
        <p className="muted mt-3 text-xs">All demo data is synthetic. {ANON ? "The merchants are illustrative stand-ins, not clients; none is live on Muse." : "BJ's pages are illustrative and not affiliated with BJ's Wholesale Club; BJ's is not live on Muse."} Umpyr connects to Muse over an existing open protocol and owns the order from the intent check before payment to the signed audit after the dispute; shipping and logistics stay with the merchant.</p>
        {!PUBLIC_DEMO && <div className="mt-3"><Link className="btn" to="/try">Try your data</Link></div>}
        {workspaces.length > 0 && !PUBLIC_DEMO && (
          <ul className="mt-4 grid gap-2 sm:grid-cols-2">
            {workspaces.map((w) => (
              <li key={w.id} className="panel p-3">
                <div className="flex items-center justify-between">
                  <Link to={`/${w.id}`} className="font-medium">{w.name}</Link>
                  <span className="badge">{w.status}</span>
                </div>
                <div className="muted text-xs mt-1">{w.kind}{w.is_synthetic ? " · synthetic data" : ""}</div>
              </li>
            ))}
          </ul>
        )}
      </section>
      <aside className="panel p-4 text-sm">
        <h2 className="font-semibold">Engine</h2>
        {error && <p className="mt-2 text-amber-600">API unreachable: {error}</p>}
        {health && (
          <dl className="mt-2 grid grid-cols-2 gap-y-1">
            <dt className="muted">status</dt><dd>{health.status} · db {health.db ? "ok" : "down"}</dd>
            <dt className="muted">provider</dt><dd>{health.llm_provider}</dd>
            <dt className="muted">model</dt><dd className="font-mono text-xs">{health.model}</dd>
            <dt className="muted">daily cap</dt><dd>${health.daily_cost_cap_usd.toFixed(2)}</dd>
          </dl>
        )}
        {spend && (
          <div className="mt-3">
            <div className="flex items-center justify-between">
              <span className="muted">Spend today (UTC)</span>
              <span className="font-mono">${spend.spent_usd.toFixed(4)}</span>
            </div>
            <div className="mt-1 h-2 w-full rounded" style={{ background: "var(--border)" }}>
              <div className="h-2 rounded" style={{ width: `${Math.min(100, (100 * spend.spent_usd) / Math.max(spend.cap_usd, 1e-9))}%`, background: spend.blocked ? "var(--warn)" : "var(--accent)" }} />
            </div>
            {spend.blocked && <p className="mt-2 badge badge-amber">Demo budget reached for today</p>}
          </div>
        )}
        <p className="muted mt-3 text-xs">{merchantName("rockler")} and {merchantName("alaska")} run on the live engine (Haiku, daily cap). The {merchantName("bjs")} pages run deterministic simulations over versioned fixtures behind the same request/response contracts, so no model calls and no spend.</p>
      </aside>
    </div>
  );
}
