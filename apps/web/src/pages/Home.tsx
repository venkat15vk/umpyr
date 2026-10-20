import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, PUBLIC_DEMO, type Health, type Spend, type Workspace } from "../lib/api";

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
          Umpyr resolves customer disputes with an approved procedure, compiled policy clauses, and deterministic rules. Pick a sandbox
          merchant or try your own export.
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          <Link className="btn btn-primary" to="/rockler">Rockler sandbox</Link>
          <Link className="btn btn-primary" to="/alaska">Alaska sandbox</Link>
          {!PUBLIC_DEMO && <Link className="btn" to="/try">Try your data</Link>}
        </div>
        {workspaces.length > 0 && (
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
      </aside>
    </div>
  );
}
