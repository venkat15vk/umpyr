"""Umpyr Check ontology (umpyr-check/0.1): the entities a pre-payment risk-and-intent decision is made over.

Mirrors apps/web/src/lib/bjs/types.ts. The web demo evaluates these shapes locally; the engine will accept the
same `CheckRequest` at POST /check/decisions and return the same `CheckDecision`. Customer data is masked:
tokens and hashes only, never raw PII.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CheckModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


Binding = Literal["specified", "delegated", "unspecified"]
IntentOutcome = Literal["match", "benign_drift", "material_mismatch", "unverifiable"]
CheckDecisionKind = Literal["approve", "step_up", "hold", "decline"]
Unit = Literal["each", "pack", "case", "gallon"]
FulfilmentMethod = Literal["same_day_delivery", "club_pickup", "curbside", "in_club", "ship", "gas"]


class AgentHistory(CheckModel):
    orders_90d: int
    merchants_90d: int
    avg_basket_usd: Decimal
    dispute_rate: float
    first_seen_days_ago: int
    typical_departments: list[str] = Field(default_factory=list)


class Agent(CheckModel):
    """The personal AI agent placing the order (Meta Muse, OpenAI Dots, Google Gemini, ...)."""

    platform: str
    agent_id: str = Field(..., description="Opaque id issued by the platform")
    protocol: str = Field(..., description="ap2/0.1 | acp/1.0 | stripe-link-agent/1 | unknown")
    attested: bool = Field(..., description="Platform attestation present and valid")
    attestation_issuer: str | None = None
    fingerprint_id: str = Field(..., description="Umpyr's own behavioural fingerprint of the agent")
    history: AgentHistory


class MandateItem(CheckModel):
    sku: str | None = None
    description: str
    quantity: int
    unit: Unit
    max_unit_price: Decimal | None = None
    brand: str | None = None
    substitutable: bool = False


class MandateSignature(CheckModel):
    alg: Literal["ES256", "EdDSA"]
    kid: str
    valid: bool = Field(..., description="Deterministic verification result")
    issuer: str


class Window(CheckModel):
    start: str
    end: str


class Mandate(CheckModel):
    """AP2-style intent / checkout mandate: what the customer authorised."""

    mandate_id: str
    kind: Literal["intent", "checkout"]
    binding: Binding
    confirmed: bool = Field(..., description="Customer explicitly confirmed this cart (checkout) vs intent only")
    issued_at: str
    expires_at: str
    price_ceiling: Decimal
    currency: Literal["USD"] = "USD"
    items: list[MandateItem] = Field(default_factory=list)
    substitutions_allowed: bool = False
    fulfilment: FulfilmentMethod
    delivery_window: Window | None = None
    signature: MandateSignature
    natural_language: str | None = Field(None, description="What the customer said, masked")


class CartLine(CheckModel):
    sku: str
    description: str
    brand: str | None = None
    department: str
    quantity: int
    unit: Unit
    pack_size: int | None = None
    unit_price: Decimal
    line_total: Decimal
    substituted_for: str | None = None


class Cart(CheckModel):
    lines: list[CartLine]
    fees: Decimal
    tax: Decimal
    total: Decimal
    fulfilment: FulfilmentMethod
    delivery_window: Window | None = None
    club_id: str


class MaskedCard(CheckModel):
    bin_hash: str
    last4_hash: str
    issuer_country: str
    funding: Literal["credit", "debit", "prepaid"]
    age_days_on_file: int


class Velocity(CheckModel):
    orders_24h: int
    orders_30d: int
    cards_7d: int
    addresses_30d: int
    declines_7d: int


class MembershipSignals(CheckModel):
    fingerprints_30d: int = Field(..., description="Distinct agent fingerprints seen on this membership in 30 days")
    regions_30d: list[str] = Field(default_factory=list)
    agents_30d: int


class CustomerHistory(CheckModel):
    avg_basket_usd: Decimal
    typical_departments: list[str] = Field(default_factory=list)
    chargebacks_12m: int
    returns_rate: float
    disputes_12m: int


class MaskedCustomer(CheckModel):
    """Never raw PII: member token, hashes, and aggregates."""

    member_token: str
    tier: Literal["club", "club_plus"]
    tenure_days: int
    email_hash: str
    device_hash: str
    card: MaskedCard
    velocity: Velocity
    membership: MembershipSignals
    history: CustomerHistory
    shipping_region: str
    ip_region: str
    ip_is_datacenter: bool
    device_age_days: int


class CheckContext(CheckModel):
    peer_orders_same_sku_24h: int
    sku_demand_tier: Literal["normal", "high", "constrained"]
    promo_codes: list[str] = Field(default_factory=list)
    promo_stack_depth: int = 0


class CheckRequest(CheckModel):
    protocol: Literal["umpyr-check/0.1"] = "umpyr-check/0.1"
    request_id: str
    merchant_id: str
    order_ref: str
    at: str
    agent: Agent
    mandate: Mandate
    cart: Cart
    customer: MaskedCustomer
    context: CheckContext


class LineDiff(CheckModel):
    dimension: Literal["quantity", "pack_size", "brand", "price", "substitution", "fulfilment", "ceiling", "item"]
    mandate: str
    cart: str
    severity: Literal["none", "benign", "material"]
    note: str
    line: str | None = None


class ModelFire(CheckModel):
    name: str
    version: str
    fired: bool
    score: float | None = None
    contribution: str


class CheckAction(CheckModel):
    type: Literal["capture", "request_confirmation", "queue_review", "reject"]
    message_to_agent: dict


class Commitment(CheckModel):
    algorithm: Literal["sha256"] = "sha256"
    canonicalization: Literal["sorted-keys-json"] = "sorted-keys-json"
    hash: str
    committed: dict


class LedgerRef(CheckModel):
    seq: int
    prev_hash: str
    hash: str


class CheckDecision(CheckModel):
    protocol: Literal["umpyr-check/0.1"] = "umpyr-check/0.1"
    decision_id: str
    request_id: str
    merchant_id: str
    order_ref: str
    decision: CheckDecisionKind
    reason_codes: list[str]
    intent: dict = Field(..., description="{outcome, binding, confirmed, diffs[]}")
    mandate: dict = Field(..., description="{verified, kid, issuer}")
    scores: dict = Field(..., description="{payment_fraud, account_abuse, agent_risk} 0..100")
    models: list[ModelFire]
    actions: list[CheckAction]
    comparison: dict = Field(..., description="{ordinary_fraud_tool: {decision, because}, umpyr: {decision, because}}")
    latency_ms: int
    commitment: Commitment
    ledger: LedgerRef
    policy_pack_version: str
