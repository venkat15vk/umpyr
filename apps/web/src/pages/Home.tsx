import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, PUBLIC_DEMO, type Health, type Spend, type Workspace } from "../lib/api";

/** Demo hub: four separate pages. Check and Resolve share one ontology but are demoed as separate silos. */
const DEMOS = [
  { to: "/rockler", merchant: "Rockler", product: "Resolve", line: "Woodworking retailer. Live chat ruled by compiled return, guarantee and backorder policy; ops view shows every step.", live: true },
  { to: "/alaska", merchant: "Alaska Airlines", product: "Resolve", line: "Refunds, delays, bag guarantee and DOT rules in precedence order; options turns when the passenger must choose.", live: true },
  { to: "/bjs", merchant: "BJ's Wholesale Club", product: "Resolve", line: "Eight dispute categories on email and agent channels: rule fired, fault, PDP, terms-hash confirmation, mock BJ's execution, signed audit.", live: false },
  { to: "/bjs/check", merchant: "BJ's Wholesale Club", product: "Check", line: "Risk and intent on agentic orders before payment: mandate vs cart, fraud, abuse and agent scores, step-up, hash commitment.", live: false },
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
              <Link to={d.to} className="flex items-center gap-2 font-medium">
                <span>{d.merchant}</span>
                <span className="badge">{d.product}</span>
                {!d.live && <span className="badge badge-amber">fixtures</span>}
              </Link>
              <p className="muted mt-1 text-xs">{d.line}</p>
              <Link className="btn btn-primary mt-2 text-xs" to={d.to}>Open demo</Link>
            </li>
          ))}
        </ul>
        <p className="muted mt-3 text-xs">All demo data is synthetic. BJ's pages are illustrative and not affiliated with BJ's Wholesale Club.</p>
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
        <p className="muted mt-3 text-xs">Rockler and Alaska run on the live engine (Haiku, daily cap). The BJ's pages run deterministic simulations over versioned fixtures behind the same request/response contracts, so no model calls and no spend.</p>
      </aside>
    </div>
  );
}
