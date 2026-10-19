import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { createWorkspace } from "../lib/onboard";

export function Try() {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nav = useNavigate();
  const go = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const ws = await createWorkspace(name.trim() || "My store");
      nav(`/${ws.id}/onboard`);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="mx-auto max-w-xl">
      <section className="panel p-6">
        <h1 className="text-xl font-semibold">Try Umpyr with your own data</h1>
        <p className="muted mt-2">Upload a ticket export and your policy pages. Umpyr learns your categories, procedures and rules in minutes, shows its work, and lets you approve before anything goes live.</p>
        <ul className="muted mt-3 list-disc pl-5 text-sm">
          <li>Tickets: CSV, XLSX, JSON, JSONL (Zendesk / Gorgias style), .eml or .mbox. Up to 2,000 tickets, 20 MB.</li>
          <li>Policies: PDF, DOCX, Markdown, or a help-center URL (same domain, two levels deep).</li>
          <li>Orders (optional): CSV keyed by order number, stands in for live lookups.</li>
          <li>PII is masked on ingest. Everything is deleted after 14 days.</li>
        </ul>
        <form className="mt-4 flex gap-2" onSubmit={go}>
          <input className="input" placeholder="Workspace name (e.g. Acme Tools)" value={name} onChange={(e) => setName(e.target.value)} />
          <button className="btn btn-primary" disabled={busy}>Create workspace</button>
        </form>
        {error && <p className="mt-2 text-sm text-amber-600">{error}</p>}
      </section>
    </div>
  );
}
