/**
 * Umpyr Check: deterministic simulation of the real-time risk + intent decision.
 *
 * Contract: `decide(CheckRequest) -> CheckDecision` (protocol umpyr-check/0.1). The FastAPI engine will
 * expose the same request/response at POST /check/decisions; see adapters.ts for the boundary. Every
 * number here is a fixed, explainable function of the masked request so a presenter can flip an outcome
 * live by editing a fact. Scores are 0..100.
 */
import { canonical, sha256Hex } from "./sha256";
import type { AgentRecord, Cart, CartLine, CheckDecision, CheckDecisionKind, CheckRequest, CheckStep, IntentOutcome, LineDiff, Mandate, MandateItem, MaskedCustomer, ModelFire } from "./types";

export const CHECK_POLICY_VERSION = "bjs-check-policy/0.1";
const MODELS = {
  intake: { name: "umpyr.protocol_normalizer", version: "0.3.1" },
  mandate: { name: "umpyr.mandate_verify", version: "0.2.0" },
  intent: { name: "umpyr.intent_match", version: "0.4.2" },
  fraud: { name: "umpyr.payment_fraud", version: "0.3.0" },
  abuse: { name: "umpyr.account_abuse", version: "0.2.3" },
  agent: { name: "umpyr.agent_risk", version: "0.1.4" },
  policy: { name: "umpyr.decision_policy", version: CHECK_POLICY_VERSION },
};

const n = (s: string | number | null | undefined): number => (s === null || s === undefined || s === "" ? 0 : Number(s));
const money = (x: number): string => x.toFixed(2);

/** Deterministic pseudo-latency per step (ms), seeded by the request id so repeat runs are stable. */
function timing(seed: string, idx: number, lo: number, hi: number): number {
  const h = parseInt(sha256Hex(`${seed}:${idx}`).slice(0, 6), 16);
  return lo + (h % (hi - lo + 1));
}

// ---------- step 3: intent match ----------

/** Units a line represents, normalised to the mandate's unit so "2 packs" vs "2 cases of 12 packs" compares. */
function unitsOf(qty: number, unit: string, packSize: number | null | undefined, target: string): number {
  if (unit === target) return qty;
  if (unit === "case" && (target === "pack" || target === "each")) return qty * (packSize ?? 1);
  if ((unit === "pack" || unit === "each") && target === "case") return qty / (packSize ?? 1);
  return qty;
}

function matchLine(item: MandateItem, lines: CartLine[]): CartLine | undefined {
  if (item.sku) {
    const bySku = lines.find((l) => l.sku === item.sku || l.substituted_for === item.sku);
    if (bySku) return bySku;
  }
  const words = item.description.toLowerCase().split(/\W+/).filter((w) => w.length > 3);
  return lines.find((l) => words.some((w) => l.description.toLowerCase().includes(w)));
}

export function diffIntent(mandate: Mandate, cart: Cart): { outcome: IntentOutcome; diffs: LineDiff[] } {
  const diffs: LineDiff[] = [];
  const matched = new Set<string>();
  for (const item of mandate.items) {
    const line = matchLine(item, cart.lines);
    if (!line) {
      diffs.push({ dimension: "item", mandate: `${item.quantity} ${item.unit} ${item.description}`, cart: "not in cart", severity: mandate.binding === "delegated" ? "benign" : "material", note: "Mandated item missing from cart.", line: item.description });
      continue;
    }
    matched.add(line.sku);
    const wanted = item.quantity;
    const got = unitsOf(line.quantity, line.unit, line.pack_size, item.unit);
    if (line.unit !== item.unit && got !== wanted) {
      diffs.push({ dimension: "pack_size", mandate: `${wanted} ${item.unit}`, cart: `${line.quantity} ${line.unit}${line.pack_size ? ` of ${line.pack_size}` : ""} = ${got} ${item.unit}`, severity: "material", note: `Agent read "${wanted}" as ${line.unit}s; cart holds ${got} ${item.unit}s.`, line: line.description });
    } else if (got !== wanted) {
      const ratio = got / Math.max(wanted, 1);
      const sev: LineDiff["severity"] = ratio > 1.5 || ratio < 0.5 ? "material" : "benign";
      diffs.push({ dimension: "quantity", mandate: `${wanted} ${item.unit}`, cart: `${got} ${item.unit}`, severity: sev, note: sev === "material" ? "Quantity differs materially from the mandate." : "Small quantity difference within tolerance.", line: line.description });
    }
    if (item.brand && line.brand && item.brand.toLowerCase() !== line.brand.toLowerCase()) {
      const ok = item.substitutable && mandate.substitutions_allowed;
      diffs.push({ dimension: "brand", mandate: item.brand, cart: line.brand, severity: ok ? "benign" : "material", note: ok ? "Brand substitution permitted by the mandate." : "Brand changed; mandate does not allow substitution.", line: line.description });
    }
    if (line.substituted_for && !(item.brand && line.brand && item.brand.toLowerCase() !== line.brand.toLowerCase())) {
      const ok = item.substitutable && mandate.substitutions_allowed;
      diffs.push({ dimension: "substitution", mandate: item.description, cart: `${line.description} (for ${line.substituted_for})`, severity: ok ? "benign" : "material", note: ok ? "Substitution permitted by the mandate." : "Substitution not permitted by the mandate.", line: line.description });
    }
    if (item.max_unit_price !== null && item.max_unit_price !== undefined && n(line.unit_price) > n(item.max_unit_price)) {
      const over = (n(line.unit_price) - n(item.max_unit_price)) / n(item.max_unit_price);
      diffs.push({ dimension: "price", mandate: `≤ $${item.max_unit_price}/${item.unit}`, cart: `$${line.unit_price}/${line.unit}`, severity: over <= 0.05 ? "benign" : "material", note: over <= 0.05 ? "Within 5% price tolerance." : `Unit price ${(over * 100).toFixed(0)}% over the mandated maximum${line.substituted_for ? " after substitution" : ""}.`, line: line.description });
    }
  }
  for (const line of cart.lines) {
    if (matched.has(line.sku)) continue;
    const within = n(cart.total) <= n(mandate.price_ceiling);
    const delegated = mandate.binding === "delegated";
    diffs.push({ dimension: "item", mandate: delegated ? `delegated: ${mandate.natural_language ?? "agent chooses"}` : "not mandated", cart: `${line.quantity} ${line.unit} ${line.description}`, severity: delegated && within ? "benign" : "material", note: delegated && within ? "Agent's choice inside delegated bounds." : delegated ? "Agent's choice but the ceiling is exceeded." : "Item not covered by the mandate.", line: line.description });
  }
  if (n(cart.total) > n(mandate.price_ceiling)) {
    const over = (n(cart.total) - n(mandate.price_ceiling)) / Math.max(n(mandate.price_ceiling), 1);
    diffs.push({ dimension: "ceiling", mandate: `≤ $${mandate.price_ceiling}`, cart: `$${cart.total}`, severity: over <= 0.02 ? "benign" : "material", note: over <= 0.02 ? "Total within 2% of the ceiling (tax rounding)." : `Total exceeds the customer's price ceiling by ${(over * 100).toFixed(0)}%.` });
  }
  if (cart.fulfilment !== mandate.fulfilment) {
    diffs.push({ dimension: "fulfilment", mandate: mandate.fulfilment, cart: cart.fulfilment, severity: "material", note: "Fulfilment method differs from what the customer authorised." });
  }
  const outcome: IntentOutcome = diffs.some((d) => d.severity === "material") ? "material_mismatch" : diffs.some((d) => d.severity === "benign") ? "benign_drift" : "match";
  return { outcome, diffs };
}

// ---------- steps 4-6: scores ----------

type Contrib = { label: string; points: number };
const clamp = (x: number) => Math.max(0, Math.min(100, Math.round(x)));

export function paymentFraudScore(c: MaskedCustomer, cartTotal = 0): { score: number; contributions: Contrib[] } {
  const k: Contrib[] = [{ label: "baseline", points: 4 }];
  if (c.history.avg_basket_usd > 0 && cartTotal > 5 * c.history.avg_basket_usd) k.push({ label: `order value ${(cartTotal / c.history.avg_basket_usd).toFixed(1)}x this card's average`, points: 25 });
  if (c.ip_is_datacenter) k.push({ label: "IP is a datacenter / proxy", points: 25 });
  if (c.velocity.cards_7d >= 3) k.push({ label: `${c.velocity.cards_7d} cards in 7 days`, points: 20 });
  if (c.velocity.orders_24h >= 4) k.push({ label: `${c.velocity.orders_24h} orders in 24h`, points: 15 });
  if (c.card.age_days_on_file < 2) k.push({ label: "card added < 2 days ago", points: 15 });
  if (c.card.issuer_country !== "US") k.push({ label: `issuer country ${c.card.issuer_country}`, points: 10 });
  if (c.velocity.declines_7d > 0) k.push({ label: `${c.velocity.declines_7d} declines in 7 days`, points: Math.min(30, 10 * c.velocity.declines_7d) });
  if (c.device_age_days < 1) k.push({ label: "device first seen today", points: 10 });
  if (c.ip_region !== c.shipping_region) k.push({ label: `IP ${c.ip_region} vs ship ${c.shipping_region}`, points: 10 });
  if (c.card.funding === "prepaid") k.push({ label: "prepaid card", points: 10 });
  if (c.history.chargebacks_12m > 0) k.push({ label: `${c.history.chargebacks_12m} chargebacks in 12m`, points: 15 });
  return { score: clamp(k.reduce((s, x) => s + x.points, 0)), contributions: k };
}

export function accountAbuseScore(req: CheckRequest): { score: number; contributions: Contrib[]; flags: string[] } {
  const c = req.customer, k: Contrib[] = [{ label: "baseline", points: 5 }], flags: string[] = [];
  if (c.membership.fingerprints_30d >= 3) { k.push({ label: `${c.membership.fingerprints_30d} agent fingerprints on one membership (30d)`, points: 25 }); flags.push("ABUSE_MEMBERSHIP_SHARING"); }
  if (c.membership.regions_30d.length >= 3) { k.push({ label: `orders from ${c.membership.regions_30d.length} regions (30d)`, points: 20 }); if (!flags.includes("ABUSE_MEMBERSHIP_SHARING")) flags.push("ABUSE_MEMBERSHIP_SHARING"); }
  if (req.context.peer_orders_same_sku_24h >= 5 && req.context.sku_demand_tier !== "normal") { k.push({ label: `${req.context.peer_orders_same_sku_24h} memberships bought this ${req.context.sku_demand_tier}-demand SKU via the same agent fingerprint (24h)`, points: 35 }); flags.push("ABUSE_RESELLER_PATTERN"); }
  const total = n(req.cart.total);
  if (total > 4 * c.history.avg_basket_usd) k.push({ label: `basket ${money(total)} > 4x member average ${money(c.history.avg_basket_usd)}`, points: 10 });
  if (req.context.promo_stack_depth >= 2) { k.push({ label: `${req.context.promo_stack_depth} promotions stacked`, points: 15 }); flags.push("ABUSE_PROMO_STACK"); }
  if (c.history.returns_rate > 0.3) k.push({ label: `returns rate ${(c.history.returns_rate * 100).toFixed(0)}%`, points: 10 });
  if (c.history.disputes_12m >= 3) k.push({ label: `${c.history.disputes_12m} disputes in 12m`, points: 10 });
  return { score: clamp(k.reduce((s, x) => s + x.points, 0)), contributions: k, flags };
}

export function agentRiskScore(a: AgentRecord, m: Mandate, cart: Cart): { score: number; contributions: Contrib[]; flags: string[] } {
  const k: Contrib[] = [{ label: "baseline", points: 4 }], flags: string[] = [];
  if (!a.attested) { k.push({ label: "no platform attestation", points: 40 }); flags.push("AGENT_UNATTESTED"); }
  if (!m.signature.valid) { k.push({ label: "mandate signature invalid", points: 30 }); }
  if (a.history.first_seen_days_ago < 7) k.push({ label: `fingerprint first seen ${a.history.first_seen_days_ago}d ago`, points: 10 });
  if (a.history.dispute_rate > 0.05) k.push({ label: `agent dispute rate ${(a.history.dispute_rate * 100).toFixed(1)}%`, points: 15 });
  const depts = new Set(cart.lines.map((l) => l.department));
  const unusual = [...depts].filter((d) => !a.history.typical_departments.includes(d));
  if (unusual.length && a.history.orders_90d > 0) k.push({ label: `unusual departments for this agent: ${unusual.join(", ")}`, points: 10 });
  if (n(cart.total) > 3 * a.history.avg_basket_usd && a.history.orders_90d > 0) k.push({ label: `basket ${cart.total} > 3x this agent's average ${money(a.history.avg_basket_usd)}`, points: 10 });
  if (a.protocol === "unknown") k.push({ label: "unknown protocol", points: 10 });
  return { score: clamp(k.reduce((s, x) => s + x.points, 0)), contributions: k, flags };
}

// ---------- decision ----------

export function decide(req: CheckRequest, ledgerSeq = 1, prevHash = "0".repeat(64)): CheckDecision {
  const steps: CheckStep[] = [];
  const seed = req.request_id;
  const reasons: string[] = [];

  // 1 intake
  steps.push({ id: "intake", name: "Intake and protocol normalization", ms: timing(seed, 1, 2, 6), status: "pass", summary: `${req.agent.protocol} mandate + ${req.cart.lines.length}-line cart mapped into the Umpyr ontology (Agent, Mandate, Cart, MaskedCustomer).`, data: { model: MODELS.intake } });

  // 2 mandate verification
  const now = new Date(req.at).getTime();
  const expired = new Date(req.mandate.expires_at).getTime() < now;
  const verified = req.mandate.signature.valid && !expired;
  if (!req.mandate.signature.valid) reasons.push("MANDATE_SIG_INVALID");
  if (expired) reasons.push("MANDATE_EXPIRED");
  steps.push({ id: "mandate", name: "Mandate verification", ms: timing(seed, 2, 1, 3), status: verified ? "pass" : "fail", summary: verified ? `Signature ${req.mandate.signature.alg} kid=${req.mandate.signature.kid} valid; issuer ${req.mandate.signature.issuer}; binding=${req.mandate.binding}; confirmed=${req.mandate.confirmed}.` : `Signature check failed (${!req.mandate.signature.valid ? "invalid signature" : "expired"}); mandate cannot be trusted.`, data: { model: MODELS.mandate, kid: req.mandate.signature.kid, issuer: req.mandate.signature.issuer, deterministic: true } });

  // 3 intent
  const intent = verified ? diffIntent(req.mandate, req.cart) : { outcome: "unverifiable" as IntentOutcome, diffs: [] as LineDiff[] };
  for (const d of intent.diffs) {
    if (d.severity !== "material") continue;
    const code = d.dimension === "pack_size" ? "INTENT_PACK_SIZE_DRIFT" : d.dimension === "quantity" ? "INTENT_QTY_DRIFT" : d.dimension === "price" ? "INTENT_PRICE_OVER_MAX" : d.dimension === "ceiling" ? "INTENT_PRICE_CEILING" : d.dimension === "brand" ? "INTENT_BRAND_MISMATCH" : d.dimension === "substitution" ? "INTENT_SUBSTITUTION_NOT_ALLOWED" : d.dimension === "fulfilment" ? "INTENT_FULFILMENT_MISMATCH" : "INTENT_ITEM_NOT_MANDATED";
    if (!reasons.includes(code)) reasons.push(code);
  }
  if (intent.outcome === "match") reasons.push("INTENT_MATCH");
  if (intent.outcome === "benign_drift") reasons.push(req.mandate.binding === "delegated" ? "INTENT_DELEGATED_WITHIN_BOUNDS" : "INTENT_BENIGN_DRIFT");
  steps.push({ id: "intent", name: "Intent match (mandate vs cart)", ms: timing(seed, 3, 4, 11), status: intent.outcome === "match" ? "pass" : intent.outcome === "benign_drift" ? "info" : intent.outcome === "unverifiable" ? "fail" : "warn", summary: intent.outcome === "unverifiable" ? "Skipped: no trustworthy mandate to compare against." : `${intent.outcome.replace("_", " ")}: ${intent.diffs.length} difference${intent.diffs.length === 1 ? "" : "s"} across ${req.mandate.items.length} mandated item${req.mandate.items.length === 1 ? "" : "s"}.`, data: { model: MODELS.intent, diffs: intent.diffs } });

  // 4-6 scores
  const pf = paymentFraudScore(req.customer, n(req.cart.total));
  steps.push({ id: "fraud", name: "Payment-fraud score", ms: timing(seed, 4, 3, 9), status: pf.score >= 70 ? "fail" : pf.score >= 45 ? "warn" : "pass", summary: `${pf.score}/100 from ${pf.contributions.length - 1} signal${pf.contributions.length === 2 ? "" : "s"} (card, device, velocity, geography).`, data: { model: MODELS.fraud, contributions: pf.contributions } });
  const ab = accountAbuseScore(req);
  steps.push({ id: "abuse", name: "Account and abuse score", ms: timing(seed, 5, 3, 8), status: ab.score >= 70 ? "fail" : ab.score >= 45 ? "warn" : "pass", summary: `${ab.score}/100 (membership sharing, reseller / bulk-arbitrage, promo stacking).`, data: { model: MODELS.abuse, contributions: ab.contributions } });
  const ag = agentRiskScore(req.agent, req.mandate, req.cart);
  steps.push({ id: "agent", name: "Agent risk score", ms: timing(seed, 6, 2, 7), status: ag.score >= 60 ? "fail" : ag.score >= 35 ? "warn" : "pass", summary: `${ag.score}/100 for fingerprint ${req.agent.fingerprint_id} (${req.agent.platform}, ${req.agent.attested ? "attested" : "unattested"}, ${req.agent.history.orders_90d} orders / 90d).`, data: { model: MODELS.agent, contributions: ag.contributions } });
  reasons.push(...ab.flags, ...ag.flags);

  // pattern anomaly for the member (not the agent)
  const cartDepts = [...new Set(req.cart.lines.map((l) => l.department))];
  const unusualForMember = cartDepts.filter((d) => !req.customer.history.typical_departments.includes(d));
  const valueAnomaly = n(req.cart.total) > 3 * req.customer.history.avg_basket_usd;
  if (unusualForMember.length && valueAnomaly) reasons.push("PATTERN_ANOMALY_MEMBER");

  // 7 insight
  const models: ModelFire[] = [
    { ...MODELS.intake, fired: true, contribution: "normalised request" },
    { ...MODELS.mandate, fired: true, score: verified ? 1 : 0, contribution: verified ? "verified" : "failed" },
    { ...MODELS.intent, fired: verified, contribution: intent.outcome },
    { ...MODELS.fraud, fired: pf.contributions.length > 1, score: pf.score, contribution: pf.contributions.filter((c) => c.label !== "baseline").map((c) => c.label).join("; ") || "no signals" },
    { ...MODELS.abuse, fired: ab.contributions.length > 1, score: ab.score, contribution: ab.contributions.filter((c) => c.label !== "baseline").map((c) => c.label).join("; ") || "no signals" },
    { ...MODELS.agent, fired: ag.contributions.length > 1, score: ag.score, contribution: ag.contributions.filter((c) => c.label !== "baseline").map((c) => c.label).join("; ") || "no signals" },
    { ...MODELS.policy, fired: true, contribution: "decision policy evaluated" },
  ];
  steps.push({ id: "insight", name: "Insight layer: models that fired", ms: timing(seed, 7, 1, 2), status: "info", summary: `${models.filter((m) => m.fired).length} of ${models.length} models contributed.`, data: { models } });

  // 8 decision policy (ordered; first matching rule decides)
  let decision: CheckDecisionKind = "approve";
  let because = "";
  if (!verified) { decision = "decline"; because = "Mandate cannot be verified: no trustworthy statement of what the customer authorised, so nothing can be captured."; }
  else if (pf.score >= 70) { decision = "decline"; because = `Payment-fraud score ${pf.score} is above the decline threshold (70).`; reasons.push("FRAUD_HIGH"); }
  else if (ab.flags.includes("ABUSE_RESELLER_PATTERN")) { decision = "hold"; because = "Reseller / bulk-arbitrage pattern across memberships behind one agent fingerprint; held for merchant review (quantity limits and member terms)."; }
  else if (ab.score >= 70) { decision = "hold"; because = `Account-abuse score ${ab.score} is above the hold threshold (70).`; reasons.push("ABUSE_HIGH"); }
  else if (!req.agent.attested) { decision = ag.score >= 60 ? "decline" : "step_up"; because = `Agent is not attested by its platform (agent risk ${ag.score}).`; }
  else if (intent.outcome === "material_mismatch") { decision = "step_up"; because = "Cart differs materially from the signed mandate; the agent must obtain explicit customer confirmation of the changed lines before capture."; }
  else if (ab.flags.includes("ABUSE_MEMBERSHIP_SHARING")) { decision = "step_up"; because = `One membership is being used by ${req.customer.membership.fingerprints_30d} agent fingerprints across ${req.customer.membership.regions_30d.join(", ")}; the member must confirm this order and the agents on the account.`; }
  else if (ag.score >= 60) { decision = "step_up"; because = `Agent risk ${ag.score}: behaviour outside this agent's history.`; reasons.push("AGENT_RISK_HIGH"); }
  else if (reasons.includes("PATTERN_ANOMALY_MEMBER")) { decision = "step_up"; because = `Order is outside this member's normal pattern (${unusualForMember.join(", ")}; $${money(n(req.cart.total))} vs average $${money(req.customer.history.avg_basket_usd)}). The mandate is valid, so one explicit confirmation by the member clears it.`; }
  else if (pf.score >= 45) { decision = "step_up"; because = `Payment-fraud score ${pf.score} is elevated (45-69): confirm with the customer rather than decline a possibly genuine order.`; reasons.push("FRAUD_ELEVATED"); }
  else if (ab.score >= 45) { decision = "step_up"; because = `Account-abuse score ${ab.score} elevated.`; reasons.push("ABUSE_ELEVATED"); }
  else if (intent.outcome === "benign_drift") { decision = "approve"; because = req.mandate.binding === "delegated" ? "Delegated mandate: the agent's choices stay inside the customer's stated bounds and ceiling." : "Minor drift inside the mandate's tolerances; approved with a note on the record."; }
  else { decision = "approve"; because = "Signed mandate verified, cart matches intent line by line, all risk scores low."; }
  steps.push({ id: "decision", name: "Decision", ms: timing(seed, 8, 1, 2), status: decision === "approve" ? "pass" : decision === "decline" ? "fail" : "warn", summary: `${decision.replace("_", "-")}: ${because}`, data: { reason_codes: reasons, policy: MODELS.policy } });

  // ordinary fraud tool: payment + account only, no mandate, no agent identity, no intent
  const ordinaryRisk = Math.max(pf.score, Math.round(ab.score * 0.6));
  const ordinary = ordinaryRisk >= 70 ? "decline" : ordinaryRisk >= 45 ? "review" : "approve";
  const ordinaryWhy = ordinary === "approve"
    ? `Card, device and velocity look normal (fraud ${pf.score}); no view of the mandate, the agent, or whether the cart is what the customer asked for.`
    : ordinary === "review"
      ? `Elevated transaction risk (${ordinaryRisk}); would route to manual review or decline, with no way to ask the customer through their agent.`
      : `Transaction risk ${ordinaryRisk}: would decline outright.`;

  // 9 commitment + ledger
  const committed = {
    protocol: "umpyr-check/0.1", request_id: req.request_id, merchant_id: req.merchant_id, order_ref: req.order_ref, at: req.at,
    decision, reason_codes: reasons, intent: { outcome: intent.outcome, binding: req.mandate.binding, confirmed: req.mandate.confirmed, diffs: intent.diffs },
    scores: { payment_fraud: pf.score, account_abuse: ab.score, agent_risk: ag.score }, mandate_id: req.mandate.mandate_id, mandate_verified: verified,
    agent_fingerprint: req.agent.fingerprint_id, cart_total: req.cart.total, policy_pack_version: CHECK_POLICY_VERSION,
    models: models.filter((m) => m.fired).map((m) => `${m.name}@${m.version}`),
  };
  const hash = sha256Hex(canonical(committed));
  const ledgerHash = sha256Hex(canonical({ seq: ledgerSeq, prev_hash: prevHash, actor: "check", action: "check.decision", result_hash: hash }));
  steps.push({ id: "commit", name: "Hash commitment and ledger write", ms: timing(seed, 9, 1, 3), status: "pass", summary: `sha256 over canonical decision JSON: ${hash.slice(0, 16)}…; ledger event #${ledgerSeq} chained to ${prevHash.slice(0, 8)}…`, data: { hash, ledger_hash: ledgerHash } });

  const decisionId = `chk_${hash.slice(0, 12)}`;
  const latency = steps.reduce((s, x) => s + x.ms, 0);
  const stepUpLines = intent.diffs.filter((d) => d.severity === "material").map((d) => ({ line: d.line ?? d.dimension, authorised: d.mandate, in_cart: d.cart, why: d.note }));
  const actions: CheckDecision["actions"] = decision === "approve"
    ? [{ type: "capture", message_to_agent: { protocol: "umpyr-check/0.1", type: "approved", decision_id: decisionId, order_ref: req.order_ref, message: "Order approved. Proceed to payment capture.", notes: intent.outcome === "benign_drift" ? intent.diffs.map((d) => d.note) : [] } }]
    : decision === "step_up"
      ? [{ type: "request_confirmation", message_to_agent: { protocol: "umpyr-check/0.1", type: "confirmation_required", decision_id: decisionId, order_ref: req.order_ref, expires_in_s: 900, present_to_customer: stepUpLines.length ? stepUpLines : [{ line: "order", authorised: req.mandate.natural_language ?? "mandate", in_cart: `$${req.cart.total} total`, why: because }], confirm_with: { method: "signed_checkout_mandate", binding: "specified", kid_hint: req.mandate.signature.kid }, message: "Before BJ's can take payment, show the customer the lines above and return a signed checkout mandate confirming them, or amend the cart to match the original mandate." } }]
      : decision === "hold"
        ? [{ type: "queue_review", message_to_agent: { protocol: "umpyr-check/0.1", type: "pending_review", decision_id: decisionId, order_ref: req.order_ref, message: "Order is pending merchant review. Do not retry or split the order; the customer will be contacted through the membership on file.", expected_within_hours: 4 } }]
        : [{ type: "reject", message_to_agent: { protocol: "umpyr-check/0.1", type: "declined", decision_id: decisionId, order_ref: req.order_ref, reason_codes: reasons, message: "Order declined. The mandate or agent identity could not be verified. The member can place this order directly or through an attested agent." } }];

  return {
    protocol: "umpyr-check/0.1", decision_id: decisionId, request_id: req.request_id, merchant_id: req.merchant_id, order_ref: req.order_ref,
    decision, reason_codes: reasons,
    intent: { outcome: intent.outcome, binding: req.mandate.binding, confirmed: req.mandate.confirmed, diffs: intent.diffs },
    mandate: { verified, kid: req.mandate.signature.kid, issuer: req.mandate.signature.issuer },
    scores: { payment_fraud: pf.score, account_abuse: ab.score, agent_risk: ag.score },
    models, actions,
    comparison: { ordinary_fraud_tool: { decision: ordinary, because: ordinaryWhy }, umpyr: { decision, because } },
    latency_ms: latency, steps,
    commitment: { algorithm: "sha256", canonicalization: "sorted-keys-json", hash, committed },
    ledger: { seq: ledgerSeq, prev_hash: prevHash, hash: ledgerHash },
    policy_pack_version: CHECK_POLICY_VERSION,
  };
}
