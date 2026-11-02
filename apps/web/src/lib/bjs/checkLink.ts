/**
 * The link between an order and the Umpyr Check decision that preceded its payment.
 *
 * An order fixture names the Check scenario that produced its decision plus the ids that differ per order (request id,
 * order ref, mandate id, agent, member). The decision is recomputed from that request with the same engine, so the
 * decision id and hash that Resolve cites are the ones Check would have committed: one system, one record.
 */
import { decide } from "./checkEngine";
import { sha256Hex } from "./sha256";
import type { CheckDecision, CheckLink, CheckRequest, ConfirmationBy, Order } from "./types";

export type CheckScenario = { id: string; title: string; agent_label: string; tagline: string; expected: string; ordinary_expected: string; resolve_order_id: string | null; request: CheckRequest };

export type CheckEvidence = {
  link: CheckLink;
  request: CheckRequest;
  decision: CheckDecision;
  scenario: CheckScenario;
  /** true when the page is showing a what-if on who answered the step-up */
  what_if: boolean;
};

const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));
const short = (s: string, n: number) => sha256Hex(s).slice(0, n);

/** The umpyr-check/0.1 request this order's Check decision was made on. */
export function checkRequestFor(order: Order, scenarios: CheckScenario[]): CheckRequest | null {
  const link = order.check;
  if (!link) return null;
  const scenario = scenarios.find((s) => s.id === link.scenario_id);
  if (!scenario) return null;
  const req = clone(scenario.request);
  req.request_id = link.request_id;
  req.order_ref = link.order_ref;
  req.at = link.at;
  if (order.agent_id) { req.agent.agent_id = order.agent_id; req.agent.fingerprint_id = `fp_${short(order.agent_id, 10)}`; }
  const tok = order.member.member_token;
  req.customer.member_token = tok;
  req.customer.email_hash = `sha256:${short("e" + tok, 24)}`;
  req.customer.device_hash = `sha256:${short("d" + tok, 24)}`;
  req.customer.card = { ...req.customer.card, bin_hash: `sha256:${short("b" + tok, 16)}`, last4_hash: `sha256:${short("l" + tok, 16)}` };
  req.customer.tier = order.member.tier;
  req.mandate.mandate_id = link.mandate_id;
  req.mandate.signature = { ...req.mandate.signature, kid: `kid_${short(link.mandate_id, 8)}` };
  // the mandate was issued minutes before this request and is valid for an hour; keep that true for the order's own timestamp
  const at = new Date(link.at).getTime();
  if (new Date(req.mandate.expires_at).getTime() < at || new Date(req.mandate.issued_at).getTime() > at) {
    req.mandate.issued_at = new Date(at - 3 * 60_000).toISOString().replace(/\.\d{3}Z$/, "Z");
    req.mandate.expires_at = new Date(at + 60 * 60_000).toISOString().replace(/\.\d{3}Z$/, "Z");
  }
  req.context = { ...req.context, prior_requests: [] };
  return req;
}

/** Check record + mandate as Resolve pulls them, optionally with a what-if on who confirmed the step-up. */
export function checkEvidenceFor(order: Order, scenarios: CheckScenario[], confirmationBy?: ConfirmationBy | null, adapterLabel?: string): CheckEvidence | null {
  const request = checkRequestFor(order, scenarios);
  const scenario = order.check ? scenarios.find((s) => s.id === order.check!.scenario_id) : undefined;
  if (!request || !scenario || !order.check) return null;
  const decision = decide(request, 1, sha256Hex(`bjs-check-ledger:${order.order_id}`), adapterLabel);
  const link = confirmationBy && confirmationBy !== order.check.confirmation_by ? withConfirmation(order.check, confirmationBy) : order.check;
  return { link, request, decision, scenario, what_if: link !== order.check };
}

/** A what-if copy of the link: the same step-up, answered by someone else. */
export function withConfirmation(link: CheckLink, by: ConfirmationBy): CheckLink {
  const member = by === "member";
  return {
    ...link,
    confirmation_by: by,
    confirmation: link.confirmation
      ? { ...link.confirmation, signed_by: member ? "member via the agent (what-if)" : by === "agent_platform" ? "platform auto-confirm (what-if)" : by === "agent" ? "the agent itself (what-if)" : "nobody (what-if)", member_signature: member, kid: member ? `kid_${short(link.mandate_id, 8)}` : "kid_platform_autoconf" }
      : null,
    note: `What-if: the same step-up, answered by ${member ? "the member with a signed checkout mandate" : by === "agent_platform" ? "the platform's auto-confirm, not the member" : by === "agent" ? "the agent itself" : "nobody"}.`,
  };
}

/** The order as the rule engine should see it under a what-if (rules read order.check.confirmation_by). */
export function orderWithConfirmation(order: Order, by: ConfirmationBy | null | undefined): Order {
  if (!by || !order.check || order.check.confirmation_by === by) return order;
  return { ...order, check: withConfirmation(order.check, by) };
}
