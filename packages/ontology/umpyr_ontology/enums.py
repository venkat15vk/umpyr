from enum import StrEnum


class Channel(StrEnum):
    email = "email"
    chat = "chat"
    phone = "phone"
    web_form = "web_form"
    agent = "agent"  # the customer's AI agent (protocol umpyr-resolve/0.1)


class Decision(StrEnum):
    """Outcome of the rule engine for a case."""

    approve = "approve"
    partial = "partial"
    deny = "deny"
    needs_fact = "needs_fact"  # a required fact is missing; ask the customer
    needs_approval = "needs_approval"  # outside the auto-approve authority
    escalate = "escalate"  # conflicting or ambiguous policy; a human decides


class Remedy(StrEnum):
    refund = "refund"
    partial_refund = "partial_refund"
    replacement = "replacement"
    reship = "reship"
    repair = "repair"
    return_label = "return_label"
    store_credit = "store_credit"
    travel_credit = "travel_credit"
    rebook = "rebook"
    points = "points"
    voucher = "voucher"
    expense_reimbursement = "expense_reimbursement"
    bag_delivery = "bag_delivery"
    cancel_order = "cancel_order"
    none = "none"


class Fault(StrEnum):
    merchant = "merchant"
    carrier = "carrier"
    vendor = "vendor"
    customer = "customer"
    airline = "airline"
    uncontrollable = "uncontrollable"  # weather, ATC, etc.
    unknown = "unknown"


class Tier(StrEnum):
    """Precedence tiers, highest first: law > contract > commitment > goodwill."""

    law = "law"
    contract = "contract"
    commitment = "commitment"
    goodwill = "goodwill"


TIER_RANK: dict[Tier, int] = {Tier.law: 0, Tier.contract: 1, Tier.commitment: 2, Tier.goodwill: 3}


class PaymentMethod(StrEnum):
    card = "card"
    rewards_card = "rewards_card"  # merchant-branded rewards credit card
    paypal = "paypal"
    gift_card = "gift_card"
    store_credit = "store_credit"
    travel_credit = "travel_credit"
    points = "points"
    other = "other"


class ItemCondition(StrEnum):
    new_unopened = "new_unopened"
    opened_unused = "opened_unused"
    used = "used"
    damaged = "damaged"
    defective = "defective"
    unknown = "unknown"


class ShipmentStatus(StrEnum):
    pending = "pending"
    in_transit = "in_transit"
    delivered = "delivered"
    lost = "lost"
    damaged = "damaged"
    returned = "returned"


class FlightStatus(StrEnum):
    scheduled = "scheduled"
    on_time = "on_time"
    delayed = "delayed"
    cancelled = "cancelled"
    diverted = "diverted"
    completed = "completed"


class DelayCause(StrEnum):
    controllable = "controllable"  # airline-caused: crew, maintenance, IT
    uncontrollable = "uncontrollable"  # weather, ATC, security
    unknown = "unknown"


class BagStatus(StrEnum):
    delivered = "delivered"
    delayed = "delayed"
    lost = "lost"
    damaged = "damaged"
    pilfered = "pilfered"
