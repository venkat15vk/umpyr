import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { categories, drift, patchCategory, patchProcedure, patchRule, procedures, readiness, rules, setDrift, summary, type Category, type Clause, type Drift, type Procedure, type RuleRow } from "../lib/onboard";

type Tab = "categories" | "procedures" | "rules" | "drift";

export function Setup() {
  const { client: ws = "" } = useParams();
  const [tab, setTab] = useState<Tab>("categories");
  const [name, setName] = useState(ws);
  const [cats, setCats] = useState<Category[]>([]);
  const [procs, setProcs] = useState<Procedure[]>([]);
  const [pack, setPack] = useState<{ version: string | null; clauses: Clause[]; rules: RuleRow[]; conflicts: string[] }>({ version: null, clauses: [], rules: [], conflicts: [] });
  const [findings, setFindings] = useState<Drift[]>([]);
  const [ready, setReady] = useState<{ ready: boolean; reasons: string[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mergeTarget, setMergeTarget] = useState<Record<string, string>>({});

  const load = async () => {
    try {
      const [s, c, p, r, d, rd] = await Promise.all([summary(ws), categories(ws), procedures(ws), rules(ws), drift(ws), readiness(ws)]);
      setName(s.workspace.name);
      setCats(c);
      setProcs(p);
      setPack(r);
      setFindings(d);
      setReady(rd);
    } catch (e) {
      setError(String(e));
    }
  };
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ws]);

  const act = (fn: () => Promise<unknown>) => fn().then(load).catch((e) => setError(String(e)));
  const clauseText = (id: string) => pack.clauses.find((c) => c.clause_id === id);

  return (
    <div className="space-y-3">
      <div className="panel flex flex-wrap items-center gap-3 p-3 text-sm">
        <h1 className="text-lg font-semibold">{name}</h1>
        <span className="muted">setup &amp; approvals</span>
        {ready && (ready.ready ? <span className="badge">ready for chat</span> : <span className="badge badge-amber">{ready.reasons.join(" · ")}</span>)}
        <div className="ml-auto flex gap-2">
          {(["categories", "procedures", "rules", "drift"] as Tab[]).map((t) => <button key={t} className={`btn ${tab === t ? "btn-primary" : ""}`} onClick={() => setTab(t)}>{t}</button>)}
          <Link className={`btn ${ready?.ready ? "btn-primary" : ""}`} to={`/${ws}`}>Open chat →</Link>
        </div>
      </div>
      {error && <p className="text-sm text-amber-600">{error}</p>}

      {tab === "categories" && (
        <section className="panel p-3 text-sm">
          <p className="muted">Learned from your tickets. Pain score = volume × average hours to resolve. Approve, rename or merge.</p>
          <table className="mt-2 w-full text-xs">
            <thead className="muted text-left"><tr><th>Category</th><th>Volume</th><th>Avg hours</th><th>Pain</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {cats.map((c) => (
                <tr key={c.key} className="border-t align-top" style={{ borderColor: "var(--border)" }}>
                  <td className="py-1 pr-2"><div className="font-medium">{c.name}</div><div className="muted">{c.description}</div><div className="muted font-mono">{c.key}</div></td>
                  <td className="py-1 pr-2">{c.volume}</td><td className="py-1 pr-2">{c.avg_hours_to_resolve ?? "—"}</td><td className="py-1 pr-2">{c.pain_score}</td>
                  <td className="py-1 pr-2"><span className={`badge ${c.status === "approved" ? "" : "badge-amber"}`}>{c.status}</span></td>
                  <td className="py-1 space-x-1 whitespace-nowrap">
                    {c.status !== "merged" && c.status !== "approved" && <button className="btn" onClick={() => act(() => patchCategory(ws, c.key, { status: "approved" }))}>Approve</button>}
                    {c.status !== "merged" && (
                      <>
                        <select className="input !w-auto !py-0.5" value={mergeTarget[c.key] ?? ""} onChange={(e) => setMergeTarget({ ...mergeTarget, [c.key]: e.target.value })}>
                          <option value="">merge into…</option>
                          {cats.filter((x) => x.key !== c.key && x.status !== "merged").map((x) => <option key={x.key} value={x.key}>{x.name}</option>)}
                        </select>
                        {mergeTarget[c.key] && <button className="btn" onClick={() => act(() => patchCategory(ws, c.key, { merge_into: mergeTarget[c.key] }))}>Merge</button>}
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {tab === "procedures" && (
        <section className="space-y-2 text-sm">
          {procs.map((p) => (
            <div key={p.category_key} className="panel p-3">
              <div className="flex items-center gap-2">
                <span className="font-medium">{p.name}</span><span className="muted font-mono">{p.category_key}@{p.version}</span>
                <span className={`badge ${p.status === "approved" ? "" : "badge-amber"}`}>{p.status}</span>
                <span className="muted text-xs">mined from {p.mining?.cases ?? "?"} cases; common path: {(p.mining?.common_path ?? []).join(" → ") || "—"}</span>
                <div className="ml-auto">{p.status === "approved" ? <button className="btn" onClick={() => act(() => patchProcedure(ws, p.category_key, "draft"))}>Revoke</button> : <button className="btn btn-primary" onClick={() => act(() => patchProcedure(ws, p.category_key, "approved"))}>Approve</button>}</div>
              </div>
              <ol className="mt-2 space-y-1 text-xs">
                {p.steps.map((s) => (
                  <li key={s.step_id} className="flex flex-wrap gap-2"><span className="badge" style={{ background: "var(--border)", color: "var(--muted)" }}>{s.kind}</span><span className="font-medium">{s.name}</span><span className="muted">{s.instruction}</span>{s.clause_ids.length > 0 && <span className="font-mono">{s.clause_ids.join(", ")}</span>}{s.required_facts.length > 0 && <span className="muted font-mono">{s.required_facts.join(", ")}</span>}</li>
                ))}
              </ol>
            </div>
          ))}
          {procs.length === 0 && <p className="muted">No procedures yet.</p>}
        </section>
      )}

      {tab === "rules" && (
        <section className="panel p-3 text-sm">
          <p className="muted">Rule pack {pack.version ?? "—"}: every rule cites a clause with its source text. Disable rules that do not reflect your policy. {pack.conflicts.length > 0 && <span className="text-amber-600">{pack.conflicts.length} potential conflicts flagged for review.</span>}</p>
          <ul className="mt-2 space-y-2 text-xs">
            {pack.rules.map((r) => {
              const cl = clauseText(r.clause_id);
              return (
                <li key={r.rule_id} className="rounded-md border p-2" style={{ borderColor: "var(--border)", opacity: r.enabled ? 1 : 0.55 }}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono">{r.rule_id}</span><span className="badge" style={{ background: "var(--border)", color: "var(--muted)" }}>{r.tier} p{r.priority}</span>
                    <span className={`badge ${r.effect.decision === "deny" ? "badge-amber" : ""}`}>{r.effect.decision}{r.effect.remedy ? ` / ${r.effect.remedy}` : ""}</span>
                    {r.claim_reasons.length > 0 && <span className="muted">{r.claim_reasons.join(", ")}</span>}
                    <label className="ml-auto flex items-center gap-1"><input type="checkbox" checked={r.enabled} onChange={(e) => act(() => patchRule(ws, r.rule_id, e.target.checked))} /> enabled</label>
                  </div>
                  <div className="mt-1">{r.description}</div>
                  {cl && <div className="muted mt-1">“{cl.text}” <a className="underline" href={cl.source_url} target="_blank" rel="noreferrer">{cl.clause_id}</a>{cl.ambiguous && <span className="badge badge-amber ml-1">ambiguous</span>}</div>}
                  <div className="muted mt-1 font-mono">when {JSON.stringify(r.when)}</div>
                </li>
              );
            })}
            {pack.rules.length === 0 && <li className="muted">No rules compiled (no policy documents).</li>}
          </ul>
        </section>
      )}

      {tab === "drift" && (
        <section className="panel p-3 text-sm">
          <p className="muted">Policy vs practice. Drift = staff decided differently from policy; unwritten rules = consistent practice with no clause. Decide what to do with each.</p>
          <ul className="mt-2 space-y-2 text-xs">
            {findings.map((f) => (
              <li key={f.id} className="rounded-md border p-2" style={{ borderColor: "var(--border)" }}>
                <div className="flex flex-wrap items-center gap-2"><span className={`badge ${f.kind === "drift" ? "badge-amber" : ""}`}>{f.kind}</span><span className="muted font-mono">{f.category_key}</span>{f.rule_id && <span className="font-mono">{f.rule_id}</span>}<span className="muted">support {f.support}</span><span className="badge ml-auto" style={{ background: "var(--border)", color: "var(--muted)" }}>{f.status}</span></div>
                <div className="mt-1">{f.pattern}</div>
                <div className="mt-1 flex flex-wrap gap-1">{["policy_is_right", "update_policy", "adopt_as_rule", "ignored"].map((s) => <button key={s} className={`btn !px-2 !py-0.5 ${f.status === s ? "btn-primary" : ""}`} onClick={() => act(() => setDrift(ws, f.id, s))}>{s.replace(/_/g, " ")}</button>)}</div>
              </li>
            ))}
            {findings.length === 0 && <li className="muted">No findings.</li>}
          </ul>
        </section>
      )}
    </div>
  );
}
