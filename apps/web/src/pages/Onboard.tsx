import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../lib/api";
import { addPolicyUrl, approveMapping, listUploads, loadSample, policyDocs, startOnboarding, subscribeJob, summary, uploadFile, type JobEvent, type Upload } from "../lib/onboard";

const TARGET_LABELS: Record<string, string> = { ticket_id: "Ticket id", created_at: "Created at", channel: "Channel", subject: "Subject", thread: "Conversation / first message", agent_actions: "Agent actions", resolution: "Resolution", amount: "Amount", order_id: "Order id", customer_name: "Customer name", customer_email: "Customer email", staff_reply: "Staff reply column", placed_at: "Order date", status: "Status", total: "Total", subtotal: "Subtotal", shipping_total: "Shipping", payment_method: "Payment method", customer_id: "Customer id", item_name: "Item name", item_sku: "Item SKU", item_category: "Item category", item_price: "Item price", item_quantity: "Item quantity", shipped_at: "Shipped at", delivered_at: "Delivered at", carrier: "Carrier" };

function MappingCard({ ws, up, onDone }: { ws: string; up: Upload; onDone: () => void }) {
  const mp = up.mapping!;
  const [edits, setEdits] = useState<Record<string, string | null>>(Object.fromEntries(Object.entries(mp.mapping).map(([t, v]) => [t, v.source])));
  const [fmt, setFmt] = useState(mp.thread_format);
  const [busy, setBusy] = useState(false);
  const approved = mp.status === "approved";
  const approve = async () => {
    setBusy(true);
    try {
      await approveMapping(ws, up.id, edits, fmt);
      onDone();
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="mt-2 rounded-md border p-2 text-xs" style={{ borderColor: "var(--border)" }}>
      <div className="flex items-center gap-2">
        <span className="font-medium">Column mapping</span>
        <span className={`badge ${approved ? "" : "badge-amber"}`}>{approved ? "approved" : "proposed by the mapping agent"}</span>
        {mp.kind === "tickets" && (
          <label className="ml-auto flex items-center gap-1"><span className="muted">conversation format</span>
            <select className="input !w-auto !py-0.5" value={fmt} onChange={(e) => setFmt(e.target.value)} disabled={approved}>
              <option value="json_list">JSON list of messages</option><option value="plain_text">plain text</option><option value="split_columns">customer + staff columns</option><option value="unknown">unknown</option>
            </select>
          </label>
        )}
      </div>
      {mp.notes && <p className="muted mt-1">{mp.notes}</p>}
      <table className="mt-2 w-full">
        <thead className="muted text-left"><tr><th>Umpyr field</th><th>Your column</th><th>Confidence</th><th>Sample</th></tr></thead>
        <tbody>
          {Object.entries(mp.mapping).map(([target, v]) => (
            <tr key={target} className="border-t" style={{ borderColor: "var(--border)" }}>
              <td className="py-1 pr-2">{TARGET_LABELS[target] ?? target}</td>
              <td className="py-1 pr-2">
                <select className="input !py-0.5" value={edits[target] ?? ""} onChange={(e) => setEdits({ ...edits, [target]: e.target.value || null })} disabled={approved}>
                  <option value="">— none —</option>
                  {mp.columns.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </td>
              <td className="py-1 pr-2"><span style={{ color: v.confidence >= 0.8 ? "var(--accent)" : v.confidence > 0 ? "var(--warn)" : "var(--muted)" }}>{v.source ? `${Math.round(v.confidence * 100)}%` : "—"}</span> <span className="muted">{v.note}</span></td>
              <td className="py-1 muted truncate max-w-[16rem]">{edits[target] && mp.samples?.[0] ? String(mp.samples[0][edits[target]!] ?? "") : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!approved && <button className="btn btn-primary mt-2" onClick={approve} disabled={busy}>Approve mapping</button>}
    </div>
  );
}

export function Onboard() {
  const { client: ws = "" } = useParams();
  const [name, setName] = useState(ws);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [docs, setDocs] = useState<{ title: string; source_url: string | null; source_type: string; tier: string; chars: number }[]>([]);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [jobDone, setJobDone] = useState<Record<string, unknown> | null>(null);
  const unsub = useRef<() => void>();

  const refresh = async () => {
    setUploads(await listUploads(ws));
    setDocs(await policyDocs(ws));
  };
  useEffect(() => {
    summary(ws).then((s) => setName(s.workspace.name)).catch(() => {});
    refresh().catch((e) => setError(String(e)));
    return () => unsub.current?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ws]);

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setError(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError && e.isBudgetExceeded ? "Demo budget reached for today" : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const onFile = (kind: string) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) run(kind, () => uploadFile(ws, kind, f));
    e.target.value = "";
  };

  const start = () =>
    run("start", async () => {
      const { job_id } = await startOnboarding(ws);
      setEvents([]);
      setJobDone(null);
      unsub.current = subscribeJob(job_id, (ev) => setEvents((x) => [...x, ev]), (job) => setJobDone(job as Record<string, unknown>));
    });

  const ticketsReady = uploads.some((u) => u.kind === "tickets" && u.mapping?.status === "approved");
  const progress = events.length ? Math.max(...events.map((e) => e.progress ?? 0)) : 0;
  const failed = jobDone && (jobDone as { status?: string }).status === "failed";

  return (
    <div className="grid gap-3 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
      <section className="space-y-3">
        <div className="panel p-4">
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-semibold">{name}</h1>
            <span className="badge">onboarding</span>
            <span className="muted text-xs">deleted automatically after 14 days</span>
            <button className="btn ml-auto" onClick={() => run("sample", () => loadSample(ws, "rockler"))} disabled={!!busy}>Use Rockler sample files</button>
          </div>
          {error && <p className="mt-2 text-sm text-amber-600">{error}</p>}
        </div>

        <div className="panel p-4">
          <h2 className="font-semibold">1. Tickets</h2>
          <p className="muted text-sm">CSV, XLSX, JSON, JSONL, .eml or .mbox. The mapping agent proposes how your columns map to Umpyr's ticket schema; approve or correct it.</p>
          <input className="mt-2 text-sm" type="file" accept=".csv,.xlsx,.json,.jsonl,.eml,.mbox" onChange={onFile("tickets")} disabled={!!busy} />
          {uploads.filter((u) => u.kind === "tickets").map((u) => (
            <div key={u.id} className="mt-2 text-sm">
              <span className="font-medium">{u.filename}</span> <span className="muted">{u.row_count ?? "?"} rows · {u.status}</span>
              {u.mapping && u.mapping.mapping && Object.keys(u.mapping.mapping).length > 0 && <MappingCard ws={ws} up={u} onDone={refresh} />}
            </div>
          ))}
        </div>

        <div className="panel p-4">
          <h2 className="font-semibold">2. Policies</h2>
          <p className="muted text-sm">PDF, DOCX, Markdown, HTML, or a help-center URL (same domain, two levels).</p>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
            <input type="file" accept=".pdf,.docx,.md,.markdown,.txt,.html,.htm" onChange={onFile("policy")} disabled={!!busy} />
            <input className="input !w-80" placeholder="https://www.example.com/return-policy" value={url} onChange={(e) => setUrl(e.target.value)} />
            <button className="btn" onClick={() => run("url", () => addPolicyUrl(ws, url, "contract"))} disabled={!!busy || !url}>Fetch URL</button>
          </div>
          <ul className="mt-2 text-sm">
            {docs.map((d, i) => <li key={i}><span className="font-medium">{d.title}</span> <span className="muted">{d.source_type} · {d.tier} · {d.chars.toLocaleString()} chars{d.source_url ? ` · ${d.source_url}` : ""}</span></li>)}
            {docs.length === 0 && <li className="muted">No policy documents yet. Without policies, rulings will escalate to a human.</li>}
          </ul>
        </div>

        <div className="panel p-4">
          <h2 className="font-semibold">3. Orders (optional)</h2>
          <p className="muted text-sm">CSV keyed by order number; one row per line item is fine.</p>
          <input className="mt-2 text-sm" type="file" accept=".csv,.xlsx,.json,.jsonl" onChange={onFile("orders")} disabled={!!busy} />
          {uploads.filter((u) => u.kind === "orders").map((u) => (
            <div key={u.id} className="mt-2 text-sm">
              <span className="font-medium">{u.filename}</span> <span className="muted">{u.row_count ?? "?"} rows · {u.status}</span>
              {u.mapping && <MappingCard ws={ws} up={u} onDone={refresh} />}
            </div>
          ))}
        </div>

        <div className="panel p-4">
          <div className="flex items-center gap-3">
            <button className="btn btn-primary" onClick={start} disabled={!ticketsReady || !!busy || (events.length > 0 && !jobDone)}>Start onboarding</button>
            {!ticketsReady && <span className="muted text-sm">Upload tickets and approve the mapping first.</span>}
            {busy && <span className="muted text-sm">{busy === "sample" ? "Loading sample files and proposing mappings…" : "Working…"}</span>}
          </div>
        </div>
      </section>

      <aside className="panel p-4 text-sm">
        <h2 className="font-semibold">Live progress</h2>
        <div className="mt-2 h-2 w-full rounded" style={{ background: "var(--border)" }}><div className="h-2 rounded" style={{ width: `${progress}%`, background: failed ? "var(--warn)" : "var(--accent)" }} /></div>
        <ol className="mt-3 space-y-1 text-xs">
          {events.map((e) => <li key={e.seq}><span className="badge" style={e.level === "error" ? { background: "var(--warn-soft)", color: "var(--warn)" } : undefined}>{e.step}</span> {e.message}</li>)}
          {events.length === 0 && <li className="muted">Steps stream here: ingest → compile → mine → taxonomy → router → procedures → drift.</li>}
        </ol>
        {jobDone && !failed && <Link className="btn btn-primary mt-3" to={`/${ws}/setup`}>Review and approve →</Link>}
        {failed && <p className="mt-3 text-amber-600">Onboarding failed: {String((jobDone as { error?: string }).error)}</p>}
      </aside>
    </div>
  );
}
