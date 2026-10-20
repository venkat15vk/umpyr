import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError } from "../lib/api";
import { askMerchant, createSession, getExamples, getSummary, patchSession, sendMessage, type ChatSession, type Example, type Message, type Ops } from "../lib/resolve";
import { Link } from "react-router-dom";
import { OpsView } from "../components/OpsView";

const NAMES: Record<string, string> = { rockler: "Rockler", alaska: "Alaska Airlines" };

export function Client() {
  const { client = "" } = useParams();
  const [session, setSession] = useState<ChatSession | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [budgetHit, setBudgetHit] = useState(false);
  const [examples, setExamples] = useState<Example[]>([]);
  const [tab, setTab] = useState<"customer" | "merchant">("customer");
  const [wsName, setWsName] = useState<string>(NAMES[client] ?? client);
  const [isSynthetic, setIsSynthetic] = useState(true);
  const [merchantQ, setMerchantQ] = useState("");
  const [merchantLog, setMerchantLog] = useState<{ q: string; a: string }[]>([]);
  const endRef = useRef<HTMLDivElement>(null);

  const start = async () => {
    setError(null);
    try {
      const s = await createSession(client, { channel: "email", mode: "autonomous", auto_approve_limit: 100 });
      setSession(s);
      setMessages([]);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    start();
    getExamples(client).then(setExamples).catch(() => setExamples([]));
    getSummary(client).then((s) => { const w = s.workspace as { name: string; is_synthetic: boolean }; setWsName(w.name); setIsSynthetic(w.is_synthetic); }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client]);

  useEffect(() => {
    if (typeof endRef.current?.scrollIntoView === "function") endRef.current.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  const send = async (text: string) => {
    if (!session || !text.trim() || busy) return;
    setBusy(true);
    setError(null);
    setInput("");
    setMessages((m) => [...m, { role: "customer", text }]);
    try {
      const r = await sendMessage(session.id, text);
      setMessages((m) => [...m, { role: "assistant", text: r.reply, ops: r.ops, customer: r.customer ?? null }]);
      setSession(r.session);
    } catch (e) {
      if (e instanceof ApiError && e.isBudgetExceeded) setBudgetHit(true);
      else setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (patch: Partial<ChatSession>) => {
    if (!session) return;
    setSession(await patchSession(session.id, patch));
  };

  const ask = async () => {
    if (!merchantQ.trim() || busy) return;
    const q = merchantQ;
    setMerchantQ("");
    setBusy(true);
    try {
      const r = await askMerchant(client, q);
      setMerchantLog((l) => [...l, { q, a: r.answer }]);
    } catch (e) {
      if (e instanceof ApiError && e.isBudgetExceeded) setBudgetHit(true);
      else setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const lastOps: Ops | undefined = [...messages].reverse().find((m) => m.ops)?.ops ?? undefined;

  return (
    <div className="flex flex-col gap-3">
      <div className="panel flex flex-wrap items-center gap-3 px-3 py-2 text-sm">
        <span className="font-semibold">{wsName}</span>
        {isSynthetic ? <span className="badge badge-amber">synthetic data</span> : <Link className="badge" to={`/${client}/setup`}>setup</Link>}
        <span className="muted">·</span>
        <label className="flex items-center gap-1">
          <span className="muted">Mode</span>
          <select className="input !w-auto" value={session?.mode ?? "autonomous"} onChange={(e) => toggle({ mode: e.target.value as ChatSession["mode"] })}>
            <option value="autonomous">Autonomous</option>
            <option value="assist">Assist</option>
            <option value="shadow">Shadow</option>
          </select>
        </label>
        <label className="flex items-center gap-1">
          <span className="muted">Reply channel</span>
          <select className="input !w-auto" value={session?.channel ?? "email"} onChange={(e) => toggle({ channel: e.target.value as ChatSession["channel"] })}>
            <option value="email">Email</option>
            <option value="agent">Customer's AI agent</option>
          </select>
        </label>
        <label className="flex items-center gap-1">
          <span className="muted">Auto-approve ≤ $</span>
          <input className="input !w-24" type="number" min={0} step={25} value={session?.auto_approve_limit ?? 100} onChange={(e) => toggle({ auto_approve_limit: Number(e.target.value) })} />
        </label>
        <div className="ml-auto flex items-center gap-2">
          <button className={`btn ${tab === "customer" ? "btn-primary" : ""}`} onClick={() => setTab("customer")}>Customer chat</button>
          <button className={`btn ${tab === "merchant" ? "btn-primary" : ""}`} onClick={() => setTab("merchant")}>Merchant chat</button>
          <button className="btn" onClick={start}>New case</button>
        </div>
      </div>

      {budgetHit && <div className="panel p-3 text-sm" style={{ borderColor: "var(--warn)" }}><span className="badge badge-amber">Demo budget reached for today</span> <span className="muted">The engine stopped making model calls. Come back tomorrow (UTC) or raise the cap.</span></div>}
      {error && <div className="panel p-3 text-sm text-amber-600">{error}</div>}

      {tab === "customer" ? (
        <div className="grid gap-3 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
          <section className="panel flex min-h-[70vh] flex-col">
            <div className="flex-1 space-y-3 overflow-y-auto p-3">
              {messages.length === 0 && (
                <div className="muted text-sm">
                  <p>Write as a customer, or start from an example:</p>
                  <ul className="mt-2 space-y-1">
                    {examples.map((ex) => (
                      <li key={ex.category_key}>
                        <button className="btn w-full justify-start text-left" onClick={() => send(ex.text)}>
                          <span className="badge">{ex.category_key}</span>
                          <span className="truncate">{ex.text.slice(0, 110)}…</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {messages.map((m, i) => (
                <div key={i} className={`max-w-[92%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${m.role === "customer" ? "ml-auto" : ""}`} style={{ background: m.role === "customer" ? "var(--accent-soft)" : "var(--bg)", border: "1px solid var(--border)" }}>
                  <div className="muted mb-1 text-xs">{m.role === "customer" ? "Customer" : session?.channel === "agent" && m.ops?.agent_message ? "Umpyr → customer's agent" : "Umpyr"}{m.ops ? ` · ${m.ops.status.replace("_", " ")}` : ""}</div>
                  {m.role === "assistant" && m.customer && m.customer.status_lines.length > 0 && (
                    <div className="muted mb-1 text-xs">{m.customer.status_lines.join(" · ")}</div>
                  )}
                  {m.role === "assistant" && m.ops?.agent_message ? <pre className="overflow-x-auto text-xs">{JSON.stringify(m.ops.agent_message, null, 2)}</pre> : m.text}
                  {m.role === "assistant" && m.customer && m.customer.options.length > 1 && !m.customer.chosen_remedy && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {m.customer.options.map((o) => (
                        <button key={o.remedy} className="btn text-xs" disabled={busy} onClick={() => send(`I'll take the ${o.label}.`)}>
                          {o.label}{o.amount ? ` · $${o.amount}` : ""}{o.guaranteed_by_regulation ? " · your right" : ""}
                        </button>
                      ))}
                    </div>
                  )}
                  {m.role === "assistant" && m.customer && m.customer.queue?.sla && (
                    <div className="muted mt-1 text-xs">{m.customer.queue.overdue ? "Authorisation overdue: a specialist now owns this." : `You will hear back ${m.customer.queue.sla}.`}</div>
                  )}
                  {m.role === "assistant" && m.customer && m.customer.citations.length > 0 && (
                    <div className="muted mt-1 text-xs">
                      Sources: {m.customer.citations.map((c, j) => (
                        <span key={j}>{j > 0 ? "; " : ""}{c.url ? <a className="underline" href={c.url} target="_blank" rel="noreferrer">{c.document}</a> : c.document}{c.section ? `, ${c.section}` : ""}{c.page ? `, p.${c.page}` : ""}</span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
              {busy && <div className="muted text-sm">Umpyr is working through the procedure…</div>}
              <div ref={endRef} />
            </div>
            <form className="flex gap-2 border-t p-3" style={{ borderColor: "var(--border)" }} onSubmit={(e) => { e.preventDefault(); send(input); }}>
              <input className="input" placeholder="Describe the problem (include your order number or confirmation code)…" value={input} onChange={(e) => setInput(e.target.value)} disabled={busy || !session} />
              <button className="btn btn-primary" type="submit" disabled={busy || !session}>Send</button>
            </form>
          </section>
          <OpsView ops={lastOps} />
        </div>
      ) : (
        <section className="panel flex min-h-[70vh] flex-col">
          <div className="flex-1 space-y-3 overflow-y-auto p-3 text-sm">
            <p className="muted">Ask about your own data: "what drifts most?", "top categories by pain", "which rule fires most in drift findings?"</p>
            {merchantLog.map((x, i) => (
              <div key={i} className="space-y-1">
                <div className="ml-auto max-w-[85%] rounded-lg px-3 py-2" style={{ background: "var(--accent-soft)" }}>{x.q}</div>
                <div className="max-w-[85%] rounded-lg px-3 py-2 whitespace-pre-wrap" style={{ background: "var(--bg)", border: "1px solid var(--border)" }}>{x.a}</div>
              </div>
            ))}
            {busy && <div className="muted">Thinking…</div>}
          </div>
          <form className="flex gap-2 border-t p-3" style={{ borderColor: "var(--border)" }} onSubmit={(e) => { e.preventDefault(); ask(); }}>
            <input className="input" placeholder="Ask about categories, drift, rules…" value={merchantQ} onChange={(e) => setMerchantQ(e.target.value)} disabled={busy} />
            <button className="btn btn-primary" type="submit" disabled={busy}>Ask</button>
          </form>
        </section>
      )}
    </div>
  );
}
