import { Link } from "react-router-dom";
import type { CheckEvidence } from "../../lib/bjs/checkLink";
import { POSITION_LABEL, SPEAKER_LABEL, TERMINATIONS, type Evidence, type Negotiation, type NegotiationState, type Turn, type TurnKind } from "../../lib/bjs/negotiation";
import type { ConfirmationBy, Order } from "../../lib/bjs/types";
import { Hash, JsonBlock, Pill, RaciChips, RaciLegend } from "./ui";

const EVIDENCE_LABEL: Record<Evidence["kind"], string> = { mandate: "intent mandate", check_record: "Check record", order: "order data", policy_rule: "policy rule", history: "closed cases", attachment: "attachment" };
const EVIDENCE_TONE: Record<Evidence["kind"], "accent" | "amber" | "muted" | "red"> = { mandate: "accent", check_record: "accent", order: "muted", policy_rule: "amber", history: "muted", attachment: "muted" };
const KIND_LABEL: Record<TurnKind, string> = { claim: "opens the claim", offer: "offers", hold: "holds its position", deny: "denies, cites policy", ask_fact: "asks for evidence", provide_fact: "supplies evidence", counter: "counter-argues", refer_to_member: "escalates to the member", member_decision: "decides", accept: "accepts", needs_approval: "needs a BJ's approver", staff_approval: "approves", terms: "issues the terms hash", terms_accepted: "signs the terms hash", execute: "executes", audit: "signs the audit record", escalate: "escalates to BJ's staff", pushback: "pushes back", acknowledge: "acknowledges" };
const POSITION_TONE: Record<string, "accent" | "amber" | "muted"> = { opening: "muted", held: "amber", conceded: "accent", changed_on_evidence: "accent", accepted: "accent" };

/** Evidence cited in one turn: chips with the detail on hover, and the full list behind a toggle. */
export function EvidenceChips({ evidence }: { evidence: Evidence[] }) {
  if (!evidence.length) return null;
  return (
    <details className="mt-1 text-[11px]">
      <summary className="cursor-pointer list-none underline decoration-dotted">evidence cited ({evidence.length}): {[...new Set(evidence.map((e) => EVIDENCE_LABEL[e.kind]))].join(", ")}</summary>
      <ul className="mt-1 space-y-0.5">
        {evidence.map((e, i) => <li key={i}><Pill text={EVIDENCE_LABEL[e.kind]} tone={EVIDENCE_TONE[e.kind]} /> <span>{e.ref}</span> <span className="muted">{e.detail}</span>{e.url && !e.url.startsWith("practice://") && <a className="ml-1 underline" href={e.url} target="_blank" rel="noreferrer">source</a>}</li>)}
      </ul>
    </details>
  );
}

/** Two agents side by side: the member's agent on the left, Umpyr's agent (for BJ's) on the right; people and BJ's systems in the middle.
 *  Plain text is what the agents said; the grey monospace block underneath is the technical trail (ids, hashes, rules). */
export function Transcript({ turns, shown, compact = false, legend = true }: { turns: Turn[]; shown: number; compact?: boolean; legend?: boolean }) {
  return (
    <div className="space-y-3">
      {legend && <div className="muted flex flex-wrap items-center gap-2 text-[11px]"><span className="rounded px-1.5 py-0.5" style={{ background: "var(--accent-soft)", color: "var(--text)" }}>what the agents said</span><span className="rounded px-1.5 py-0.5 font-mono" style={{ background: "var(--bg)", borderLeft: "3px solid var(--warn)" }}>ids · hashes · rules</span></div>}
      {turns.slice(0, shown).map((t) => {
        const side = t.speaker === "muse" ? "mr-auto" : t.speaker === "umpyr" ? "ml-auto" : "mx-auto";
        const bg = t.speaker === "muse" ? "var(--panel)" : t.speaker === "umpyr" ? "var(--accent-soft)" : "var(--panel)";
        const border = t.speaker === "muse" || t.speaker === "umpyr" ? "1px solid var(--border)" : "1px dashed var(--border)";
        const showOffer = t.speaker === "umpyr" && t.offer && ["offer", "hold", "needs_approval", "terms"].includes(t.kind);
        return (
          <div key={t.n} className={`${side} max-w-[88%] rounded-lg px-3 py-2 ${compact ? "text-xs" : "text-sm"}`} style={{ background: bg, border }}>
            <div className="muted mb-1 flex flex-wrap items-center gap-2 text-[11px]">
              <span className="font-medium" style={{ color: "var(--text)" }}>{SPEAKER_LABEL[t.speaker]}</span>
              <span>{KIND_LABEL[t.kind]}</span>
              {t.position !== "n/a" && <Pill text={POSITION_LABEL[t.position]} tone={POSITION_TONE[t.position] ?? "muted"} />}
              {showOffer && <Pill text={`offer $${t.offer!.total}`} tone="muted" />}
              <span className="ml-auto font-mono">{t.n}</span>
            </div>
            <div className="whitespace-pre-wrap">{t.text}</div>
            {(t.tech.length > 0 || t.evidence.length > 0) && (
              <div className="muted mt-1.5 rounded px-2 py-1 font-mono text-[11px] leading-snug" style={{ background: "var(--bg)", borderLeft: "3px solid var(--warn)" }}>
                {t.tech.map((x, i) => <div key={i} className="break-all">{x}</div>)}
                <EvidenceChips evidence={t.evidence} />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** Open issues, offers on the table, policy limits and the three ways a negotiation can end. */
export function StatePanel({ state, shown, total }: { state: NegotiationState; shown: number; total: number }) {
  const t = state.termination;
  return (
    <section className="panel p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2"><h2 className="font-semibold">Negotiation state</h2><Pill text={`turn ${shown} of ${total}`} tone="muted" />{t && <Pill text={t.kind.replace(/_/g, " ")} tone={t.kind === "agreement" || t.kind === "ruling_accepted" ? "accent" : "amber"} />}</div>
      <div className="mt-2 grid gap-3 text-xs sm:grid-cols-2">
        <div>
          <div className="muted">Open issues</div>
          {state.open_issues.length ? <ul className="list-disc pl-4">{state.open_issues.map((x) => <li key={x}>{x}</li>)}</ul> : <div>none</div>}
          {state.muse_asks.join("|") !== state.open_issues.join("|") && <><div className="muted mt-2">Member's agent asks for</div>{state.muse_asks.length ? <ul className="list-disc pl-4">{state.muse_asks.map((x) => <li key={x}>{x}</li>)}</ul> : <div>nothing further</div>}</>}
        </div>
        <div>
          <div className="muted">Current offer from Umpyr (for BJ's)</div>
          {state.current_offer ? (
            <table className="w-full"><tbody>
              {state.current_offer.lines.map((l) => <tr key={l.issue} className="border-t align-top" style={{ borderColor: "var(--border)" }}><td className="py-0.5 pr-2">{l.label}</td><td className="py-0.5 pr-2">{l.decision === "approve" || l.decision === "partial" ? `${l.remedy} $${l.amount}` : l.decision}</td><td className="py-0.5 font-mono">{l.rule_id ?? "—"}</td></tr>)}
              <tr className="border-t font-medium" style={{ borderColor: "var(--border)" }}><td className="py-0.5">total</td><td className="py-0.5">${state.current_offer.total}</td><td /></tr>
            </tbody></table>
          ) : <div>none yet</div>}
          <div className="muted mt-2">Policy limits</div>
          <div>auto-approve ≤ ${state.policy_limits.auto_approve_limit.toFixed(2)} · mode {state.policy_limits.mode}{state.policy_limits.ceiling_hit ? ` · ceiling hit${state.policy_limits.staff_approved ? ", approved by BJ's staff" : ", waiting for a BJ's approver"}` : ""}</div>
          <div className="muted mt-1">terms {state.terms_status} · execution {state.execution_status.replace(/_/g, " ")}</div>
        </div>
      </div>
      <div className="muted mt-3 text-xs">Termination conditions</div>
      <ul className="mt-1 space-y-1 text-xs">
        {TERMINATIONS.map((x) => {
          const hit = t?.kind === x.kind || (x.kind === "agreement" && t?.kind === "ruling_accepted");
          const referral = x.kind === "escalate_customer" && state.customer_referral;
          return (
            <li key={x.kind} className="flex flex-wrap items-start gap-2">
              <Pill text={hit ? "reached" : referral ? `turn ${state.customer_referral!.turn}${state.customer_referral!.resumed ? ", resumed" : ""}` : "open"} tone={hit ? (x.kind === "agreement" ? "accent" : "amber") : referral ? "amber" : "muted"} />
              <span>{x.label}{hit && t ? <span className="muted"> · {t.reason}{t.sla ? ` · ${t.sla}` : ""}</span> : null}</span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

const CONFIRMERS: { v: ConfirmationBy; label: string }[] = [{ v: "member", label: "the member, with a signed checkout mandate" }, { v: "agent_platform", label: "the platform's auto-confirm (no member signature)" }];

/** The Check record and mandate that Resolve pulls as evidence, with a what-if on who answered the step-up. */
export function EvidencePanel({ check, order, whatIf, onWhatIf }: { check: CheckEvidence | null; order: Order; whatIf: ConfirmationBy | null; onWhatIf?: (v: ConfirmationBy | null) => void }) {
  if (!check) return <section className="panel p-3 text-sm"><h2 className="font-semibold">Evidence from Check</h2><p className="muted mt-1 text-xs">No Check decision preceded this order (placed by the member, not an agent).</p></section>;
  const d = check.decision, m = check.request.mandate, l = check.link;
  const material = d.intent.diffs.filter((x) => x.severity === "material");
  return (
    <section className="panel p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2"><h2 className="font-semibold">Evidence pulled from Check (Part 1)</h2><Pill text={d.decision.replace("_", "-")} tone={d.decision === "approve" ? "accent" : d.decision === "decline" ? "red" : "amber"} /><Pill text={`intent ${d.intent.outcome.replace("_", " ")}`} tone={d.intent.outcome === "match" ? "accent" : "amber"} />{check.what_if && <Pill text="what-if" tone="amber" />}</div>
      <div className="muted mt-1 font-mono text-[11px] break-all">{d.decision_id} · sha256 {d.commitment.hash.slice(0, 24)}… · {check.request.at.slice(0, 16).replace("T", " ")} UTC</div>
      <div className="mt-2 grid gap-3 text-xs md:grid-cols-2">
        <div>
          <div className="muted">Intent mandate {m.mandate_id} <span className="font-mono">{m.signature.alg} kid {m.signature.kid}</span></div>
          {m.natural_language && <div className="italic">“{m.natural_language}”</div>}
          <div className="muted">{m.items.map((i) => `${i.quantity} ${i.unit} ${i.description}${i.max_unit_price ? ` ≤ $${i.max_unit_price}` : ""}`).join("; ")} · ceiling ${m.price_ceiling} · substitutions {m.substitutions_allowed ? "allowed" : "off"} · {m.confirmed ? "signed by the member" : "unconfirmed"}</div>
        </div>
        <div>
          <div className="muted">Cart vs mandate at decision time</div>
          {material.length ? <ul className="list-disc pl-4">{material.map((x, k) => <li key={k}><span className="font-mono">{x.dimension}</span>: {x.mandate} → {x.cart}</li>)}</ul> : <div>matched line by line</div>}
          <div className="muted mt-1">Risk scores (table stakes): fraud {d.scores.payment_fraud} · account {d.scores.account_abuse} · agent {d.scores.agent_risk}</div>
          <div className="mt-2 muted">Step-up answered by</div>
          {d.decision === "step_up" && onWhatIf ? (
            <select className="input" value={whatIf ?? l.confirmation_by} onChange={(e) => onWhatIf(e.target.value === order.check?.confirmation_by ? null : (e.target.value as ConfirmationBy))}>
              {CONFIRMERS.map((c) => <option key={c.v} value={c.v}>{c.label}{c.v === order.check?.confirmation_by ? " (on record)" : " (what-if)"}</option>)}
            </select>
          ) : <div>{d.decision === "step_up" ? CONFIRMERS.find((c) => c.v === l.confirmation_by)?.label ?? l.confirmation_by : "no step-up: approved on the member-signed mandate"}</div>}
          <div className="muted mt-1">{l.note}</div>
        </div>
      </div>
      <div className="muted mt-2 text-xs"><Link className="underline" to={`/bjs/check?scenario=${check.scenario.id}`}>Open this record in Part 1 →</Link></div>
    </section>
  );
}

/** Fault, rulings per issue, terms hash, execution and the signed audit record for a negotiation. */
export function RulingPanel({ n, upTo }: { n: Negotiation; upTo?: number }) {
  // `upTo` = number of turns on screen; everything later in the negotiation stays hidden until it is reached
  const shown = upTo ?? n.turns.length;
  const complete = shown >= n.turns.length;
  const lastUmpyr = [...n.turns.slice(0, shown)].reverse().find((t) => t.speaker === "umpyr" && t.runs);
  const runs = lastUmpyr?.runs ?? {};
  const state = n.turns[shown - 1]?.state ?? n.final;
  const issues = Object.entries(runs);
  const fault = issues.length ? (issues.map(([, r]) => r.ruling).sort((a, b) => Number(b.amount ?? 0) - Number(a.amount ?? 0))[0].fault) : null;
  const termsVisible = state.terms_status !== "n/a";
  const execVisible = state.execution_status === "executed" || state.execution_status === "shadow";
  return (
    <section className="panel p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2"><h2 className="font-semibold">Fault, terms, execution, audit</h2>{fault && <Pill text={`fault: ${fault}`} tone={fault === "customer" ? "muted" : "amber"} />}<Pill text={`terms ${state.terms_status}`} tone={state.terms_status === "confirmed" ? "accent" : "muted"} /><Pill text={`execution ${state.execution_status.replace(/_/g, " ")}`} tone={state.execution_status === "executed" ? "accent" : "muted"} /></div>
      {!issues.length && <div className="muted mt-1 text-xs">No ruling yet: Umpyr's agent has not taken its first turn.</div>}
      <table className="mt-2 w-full text-xs">
        <thead className="muted text-left"><tr><th className="pr-2">Issue</th><th className="pr-2">Ruling</th><th className="pr-2">Rule · clause</th><th>Fault</th></tr></thead>
        <tbody>{issues.map(([k, r]) => <tr key={k} className="border-t align-top" style={{ borderColor: "var(--border)" }}><td className="py-1 pr-2">{n.case.issues.find((i) => i.key === k)?.label ?? k}</td><td className="py-1 pr-2">{r.ruling.decision}{r.ruling.remedy ? ` · ${r.ruling.remedy}` : ""}{r.ruling.amount ? ` · $${r.ruling.amount}` : ""}</td><td className="py-1 pr-2 font-mono">{r.ruling.winning_rule_id ?? "—"}</td><td className="py-1">{r.ruling.fault}</td></tr>)}</tbody>
      </table>
      {n.terms && termsVisible && (
        <div className="mt-2 space-y-1 text-xs">
          <Hash hex={n.terms.hash} label="terms sha256" />
          <details><summary className="muted cursor-pointer">terms the member's agent signed</summary><JsonBlock value={n.terms.terms} maxHeight={220} /></details>
          {n.terms.confirmation && <details><summary className="muted cursor-pointer">terms_accepted</summary><JsonBlock value={n.terms.confirmation} maxHeight={140} /></details>}
        </div>
      )}
      {n.execution.request && execVisible && (
        <div className="mt-2 grid gap-2 text-xs md:grid-cols-2">
          <div><div className="muted">request → BJ's (mock API)</div><JsonBlock value={n.execution.request} maxHeight={200} /></div>
          <div><div className="muted">response ← BJ's</div><JsonBlock value={n.execution.response} maxHeight={200} /></div>
        </div>
      )}
      {complete ? <div className="mt-2 space-y-1 text-xs">
        <Hash hex={n.audit.hash} label="audit sha256" />
        <Hash hex={n.audit.signature} label={`signature hmac-sha256 kid=${n.audit.kid}`} />
        <details><summary className="muted cursor-pointer">signed audit record</summary><JsonBlock value={n.audit.record} maxHeight={260} /></details>
        <details>
          <summary className="muted cursor-pointer">RACI per turn ({n.turns.length})</summary>
          <div className="muted mt-1"><RaciLegend /></div>
          <table className="mt-1 w-full"><tbody>{n.turns.map((t) => <tr key={t.n} className="border-t align-top" style={{ borderColor: "var(--border)" }}><td className="py-1 pr-2 whitespace-nowrap"><span className="muted font-mono">{String(t.n).padStart(2, "0")}</span> {SPEAKER_LABEL[t.speaker]} {KIND_LABEL[t.kind]}</td><td className="py-1"><RaciChips raci={t.raci} /></td></tr>)}</tbody></table>
        </details>
      </div> : <div className="muted mt-2 text-xs">The signed audit record appears when the negotiation ends.</div>}
    </section>
  );
}
