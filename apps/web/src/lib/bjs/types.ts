/**
 * Umpyr ontology as used by the BJ's demos (Resolve and Check share it).
 *
 * Extensions over packages/ontology (all additive; listed in samples/bjs/README.md):
 *   Fault              + agent, agent_platform, third_party, indeterminate
 *   Item               + pack_size, units_per_pack, is_perishable, is_fuel, unit (each|case|gallon), department
 *   Order              + fulfilment, charges[], member
 *   Fulfilment         new: same_day_delivery | club_pickup | curbside | in_club | ship, partner, substitutions_allowed, ...
 *   Member             new: tier, fee_paid, renewed_at, auto_renew, tenure_days, fingerprints_30d, regions_30d
 *   Agent              new: platform, agent_id, protocol, attested, fingerprint_id, behaviour history
 *   Mandate            new: AP2-style intent/checkout mandate
 *   Cart               new: what the agent actually built
 *   CheckDecision      new: the umpyr-check/0.1 response
 */

export type RaciLetter = "R" | "A" | "C" | "I";
export type Party = "customer" | "customer_agent" | "agent_platform" | "umpyr" | "merchant" | "merchant_staff" | "third_party";
export type Raci = Partial<Record<Party, RaciLetter>>;

export type Fault = "customer" | "agent" | "agent_platform" | "merchant" | "third_party" | "indeterminate";
export type Decision = "approve" | "partial" | "deny" | "needs_fact" | "needs_approval" | "escalate";
export type Tier = "law" | "contract" | "commitment" | "goodwill";
export const TIER_RANK: Record<Tier, number> = { law: 0, contract: 1, commitment: 2, goodwill: 3 };

// ---------- shared ontology ----------

export type FulfilmentMethod = "same_day_delivery" | "club_pickup" | "curbside" | "in_club" | "ship" | "gas";

export type Fulfilment = {
  method: FulfilmentMethod;
  partner?: string | null; // third party that physically fulfils (e.g. delivery marketplace); illustrative
  substitutions_allowed?: boolean;
  promised_at?: string | null;
  ready_at?: string | null;
  delivered_at?: string | null;
  window_start?: string | null;
  window_end?: string | null;
  club_id?: string | null;
};

export type Item = {
  item_id: string;
  sku: string;
  name: string;
  department: string; // grocery, fresh, paper, household, electronics, tire, fuel, membership, seasonal
  brand?: string | null;
  unit: "each" | "case" | "pack" | "gallon";
  pack_size?: number | null; // units per pack as sold (e.g. 12-roll pack)
  units_per_case?: number | null; // packs per case when sold by the case
  price: string; // unit price paid
  quantity: number;
  line_total: string;
  is_perishable?: boolean;
  is_fuel?: boolean;
  is_final_sale?: boolean;
  is_non_returnable?: boolean;
  return_window_days?: number | null; // category window; null = standard
  condition?: "ok" | "damaged" | "spoiled" | "missing" | "substituted" | "wrong_quantity";
  substituted_with?: string | null;
  coupon_id?: string | null;
  coupon_value?: string | null;
};

export type Charge = { charge_id: string; kind: "sale" | "preauth" | "capture" | "refund" | "membership_fee"; amount: string; status: "pending" | "posted" | "released" | "refunded"; at: string; card_token: string };

export type Member = {
  member_token: string; // masked
  tier: "club" | "club_plus";
  fee_paid: string;
  joined_at: string;
  renewed_at?: string | null;
  renewal_charged_at?: string | null;
  auto_renew: boolean;
  tenure_days: number;
  prior_claims_12m: number;
  fingerprints_30d?: number; // distinct agent fingerprints seen on this membership in 30 days
  regions_30d?: string[];
};

export type Order = {
  order_id: string;
  placed_at: string;
  placed_by: "member" | "agent";
  agent_id?: string | null;
  status: string;
  subtotal: string;
  fees_total: string;
  tax_total: string;
  total: string;
  payment_method: "card" | "bjs_card" | "ebt" | "gift_card";
  fulfilment: Fulfilment;
  items: Item[];
  charges: Charge[];
  member: Member;
  coupons_clipped?: string[];
  check_decision_id?: string | null; // when an Umpyr Check decision preceded the order
  check?: { decision_id: string; decision: string; intent_outcome: IntentOutcome; confirmation_by: "member" | "agent_platform" | "agent"; note: string } | null;
  payments?: { posted_captures: number; posted_capture_amount: string | null; pending_holds: number; largest_pending_hold: number | null };
};

export type Claim = {
  reason: string;
  requested_remedy?: string | null;
  requested_amount?: string | null;
  has_photos?: boolean | null;
  reported_within_hours?: number | null;
  description?: string | null;
  item_ids?: string[];
  quantity_intended?: number | null;
  quantity_received?: number | null;
  coupon_id?: string | null;
  advertised_price?: string | null;
  wants_cancel_membership?: boolean | null;
  used_benefits_since_renewal?: boolean | null;
  hold_age_hours?: number | null;
  cases_to_keep?: number | null; // agent order error: cases the member keeps
};

export type FactBundle = { order: Order; item: Item; claim: Claim; member: Member; fulfilment: Fulfilment; agent?: AgentRecord | null; mandate?: Mandate | null; [k: string]: unknown };

// ---------- policy pack (same schema as samples/rockler/rule_pack.json) ----------

export type Clause = {
  clause_id: string;
  title: string;
  text: string;
  source_url: string;
  source_title: string;
  fetched_at: string;
  tier: Tier;
  confirmed: boolean; // true = read on bjs.com; false = illustrative
  section?: string | null;
};

export type Rule = {
  rule_id: string;
  clause_id: string;
  tier: Tier;
  priority: number;
  categories: string[];
  claim_reasons: string[];
  requires: string[];
  when: unknown;
  effect: { decision: Decision; remedy: string | null; amount: unknown; fault: Fault; note: string };
  description: string;
  enabled: boolean;
};

export type RulePack = { workspace: string; version: string; clauses: Clause[]; rules: Rule[]; conflicts: unknown[] };

export type ProcedureStep = { step_id: string; kind: "gather" | "verify" | "check" | "decide" | "authorize" | "confirm" | "execute" | "close"; name: string; tool: string; clause_ids: string[]; instruction: string; required_facts: string[] };
export type Procedure = { category_key: string; version: number; name: string; steps: ProcedureStep[]; lifecycle_note?: string | null };

// ---------- tickets ----------

export type EmailThread = { role: "customer" | "staff"; at: string; text: string }[];
export type AgentMessage = { protocol: string; type: string; agent: { platform: string; agent_id: string }; member_token: string; order_id: string; claim: Record<string, unknown>; [k: string]: unknown };

export type Ticket = {
  ticket_id: string;
  category_key: string;
  channel: "email" | "agent";
  created_at: string;
  subject: string;
  order_id: string;
  thread?: EmailThread;
  agent_message?: AgentMessage;
  /** facts the extractor should pull from the message (what the editor starts with) */
  extracted: Claim & { item_id: string };
  is_synthetic: true;
};

export type Category = { key: string; name: string; description: string; lifecycle_note: string };

// ---------- Umpyr Check ----------

export type AgentPlatform = "Meta Muse" | "OpenAI Dots" | "Google Gemini" | "Unknown";
export type AgentProtocol = "ap2/0.1" | "acp/1.0" | "stripe-link-agent/1" | "unknown";
export type Binding = "specified" | "delegated" | "unspecified";

export type AgentRecord = {
  platform: AgentPlatform;
  agent_id: string; // opaque, issued by the platform
  protocol: AgentProtocol;
  attested: boolean; // platform attestation present and valid
  attestation_issuer?: string | null;
  fingerprint_id: string; // Umpyr's own fingerprint of this agent's behaviour
  history: { orders_90d: number; merchants_90d: number; avg_basket_usd: number; dispute_rate: number; first_seen_days_ago: number; typical_departments: string[] };
};

export type MandateItem = { sku?: string | null; description: string; quantity: number; unit: "each" | "pack" | "case" | "gallon"; max_unit_price?: string | null; brand?: string | null; substitutable: boolean };
export type Mandate = {
  mandate_id: string;
  kind: "intent" | "checkout";
  binding: Binding;
  confirmed: boolean; // customer explicitly confirmed this cart (checkout mandate) vs intent only
  issued_at: string;
  expires_at: string;
  price_ceiling: string; // total the customer authorised
  currency: "USD";
  items: MandateItem[];
  substitutions_allowed: boolean;
  fulfilment: FulfilmentMethod;
  delivery_window?: { start: string; end: string } | null;
  signature: { alg: "ES256" | "EdDSA"; kid: string; valid: boolean; issuer: string }; // deterministic verify result
  natural_language?: string | null; // what the customer said, masked
};

export type CartLine = { sku: string; description: string; brand?: string | null; department: string; quantity: number; unit: "each" | "pack" | "case" | "gallon"; pack_size?: number | null; unit_price: string; line_total: string; substituted_for?: string | null };
export type Cart = { lines: CartLine[]; fees: string; tax: string; total: string; fulfilment: FulfilmentMethod; delivery_window?: { start: string; end: string } | null; club_id: string };

export type MaskedCustomer = {
  member_token: string;
  tier: "club" | "club_plus";
  tenure_days: number;
  email_hash: string;
  device_hash: string;
  card: { bin_hash: string; last4_hash: string; issuer_country: string; funding: "credit" | "debit" | "prepaid"; age_days_on_file: number };
  velocity: { orders_24h: number; orders_30d: number; cards_7d: number; addresses_30d: number; declines_7d: number };
  membership: { fingerprints_30d: number; regions_30d: string[]; agents_30d: number };
  history: { avg_basket_usd: number; typical_departments: string[]; chargebacks_12m: number; returns_rate: number; disputes_12m: number };
  shipping_region: string;
  ip_region: string;
  ip_is_datacenter: boolean;
  device_age_days: number;
};

export type CheckRequest = {
  protocol: "umpyr-check/0.1";
  request_id: string;
  merchant_id: string;
  order_ref: string;
  at: string;
  agent: AgentRecord;
  mandate: Mandate;
  cart: Cart;
  customer: MaskedCustomer;
  context: { peer_orders_same_sku_24h: number; sku_demand_tier: "normal" | "high" | "constrained"; promo_codes: string[]; promo_stack_depth: number };
};

export type IntentOutcome = "match" | "benign_drift" | "material_mismatch" | "unverifiable";
export type LineDiff = { dimension: "quantity" | "pack_size" | "brand" | "price" | "substitution" | "fulfilment" | "ceiling" | "item"; mandate: string; cart: string; severity: "none" | "benign" | "material"; note: string; line?: string };

export type ModelFire = { name: string; version: string; fired: boolean; score?: number; contribution: string };
export type CheckDecisionKind = "approve" | "step_up" | "hold" | "decline";

export type CheckStep = { id: string; name: string; ms: number; status: "pass" | "fail" | "info" | "warn"; summary: string; data?: unknown };

export type CheckDecision = {
  protocol: "umpyr-check/0.1";
  decision_id: string;
  request_id: string;
  merchant_id: string;
  order_ref: string;
  decision: CheckDecisionKind;
  reason_codes: string[];
  intent: { outcome: IntentOutcome; binding: Binding; confirmed: boolean; diffs: LineDiff[] };
  mandate: { verified: boolean; kid: string; issuer: string };
  scores: { payment_fraud: number; account_abuse: number; agent_risk: number };
  models: ModelFire[];
  actions: { type: "capture" | "request_confirmation" | "queue_review" | "reject"; message_to_agent: Record<string, unknown> }[];
  comparison: { ordinary_fraud_tool: { decision: string; because: string }; umpyr: { decision: CheckDecisionKind; because: string } };
  latency_ms: number;
  steps: CheckStep[];
  commitment: { algorithm: "sha256"; canonicalization: "sorted-keys-json"; hash: string; committed: Record<string, unknown> };
  ledger: { seq: number; prev_hash: string; hash: string };
  policy_pack_version: string;
};
