"""Ontology entity models.

Field descriptions double as documentation for the policy compiler: Claude sees
the field catalog (path, type, description) and may only reference these paths.
Amounts are Decimal so rulings are exact.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .enums import (
    BagStatus,
    Channel,
    Decision,
    DelayCause,
    Fault,
    FlightStatus,
    ItemCondition,
    PaymentMethod,
    Remedy,
    ShipmentStatus,
)


class OntologyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class Customer(OntologyModel):
    customer_id: str | None = Field(None, description="Merchant customer identifier (masked if PII)")
    loyalty_member: bool | None = Field(None, description="Is the customer enrolled in the loyalty program")
    loyalty_tier: str | None = Field(None, description="Loyalty tier name, e.g. 'MVP Gold' or 'none'")
    account_age_days: int | None = Field(None, description="Days since the customer account was created")
    prior_claims_12m: int | None = Field(None, description="Number of prior claims in the last 12 months")


class Item(OntologyModel):
    item_id: str | None = Field(None, description="Line item identifier")
    sku: str | None = Field(None, description="Merchant SKU (opaque)")
    name: str | None = Field(None, description="Product name")
    category: str | None = Field(
        None,
        description="Product category, e.g. power_tool, machinery, router_bit, drawer_slide, hardware, "
        "lumber, finish, clearance, gift_card, plan, class",
    )
    brand: str | None = Field(None, description="Brand, e.g. SawStop, Laguna, Rockler")
    price: Decimal | None = Field(None, description="Unit price paid")
    quantity: int | None = Field(None, description="Quantity ordered")
    line_total: Decimal | None = Field(None, description="price x quantity")
    is_final_sale: bool | None = Field(None, description="Clearance / final-sale item, not returnable")
    is_non_returnable: bool | None = Field(
        None, description="Item class that the merchant never accepts back (e.g. certain machinery brands)"
    )
    is_power_tool: bool | None = Field(None, description="Item is a power tool")
    is_machinery: bool | None = Field(None, description="Item is stationary machinery")
    is_custom: bool | None = Field(None, description="Custom or made-to-order item")
    has_lifetime_guarantee: bool | None = Field(None, description="Item is covered by a lifetime guarantee")
    condition: ItemCondition | None = Field(None, description="Condition reported for the item")
    freight_required: bool | None = Field(None, description="Ships by LTL freight rather than parcel")


class Shipment(OntologyModel):
    shipment_id: str | None = Field(None, description="Shipment identifier")
    carrier: str | None = Field(None, description="Carrier name (UPS, FedEx, USPS, freight carrier)")
    method: str | None = Field(None, description="Shipping method: ground, expedited, freight")
    is_freight: bool | None = Field(None, description="Delivered by LTL freight")
    shipped_at: datetime | None = Field(None, description="When the shipment left the warehouse")
    delivered_at: datetime | None = Field(None, description="When the carrier recorded delivery")
    status: ShipmentStatus | None = Field(None, description="Current shipment status")
    days_since_delivery: int | None = Field(None, description="Days between delivery and today")
    signed_for: bool | None = Field(None, description="Delivery was signed for")
    damage_noted_on_delivery: bool | None = Field(
        None, description="Damage was noted on the delivery receipt (freight)"
    )
    shipping_paid: Decimal | None = Field(None, description="Shipping charge paid by the customer")


class Order(OntologyModel):
    order_id: str | None = Field(None, description="Merchant order number")
    placed_at: datetime | None = Field(None, description="When the order was placed")
    status: str | None = Field(
        None, description="Order status: pending, processing, shipped, delivered, backordered, cancelled"
    )
    days_since_order: int | None = Field(None, description="Days between order placement and today")
    subtotal: Decimal | None = Field(None, description="Merchandise subtotal")
    shipping_total: Decimal | None = Field(None, description="Shipping charged")
    tax_total: Decimal | None = Field(None, description="Tax charged")
    total: Decimal | None = Field(None, description="Grand total paid")
    payment_method: PaymentMethod | None = Field(None, description="How the order was paid")
    rewards_discount: Decimal | None = Field(
        None, description="Discount applied from a rewards card or program at purchase"
    )
    is_backordered: bool | None = Field(None, description="At least one item is on backorder")
    backorder_eta_days: int | None = Field(None, description="Days until backordered item is expected")
    has_shipped: bool | None = Field(None, description="Any part of the order has shipped")
    items: list[Item] = Field(default_factory=list, description="Line items")
    shipments: list[Shipment] = Field(default_factory=list, description="Shipments")


class Flight(OntologyModel):
    flight_id: str | None = Field(None, description="Segment identifier")
    flight_number: str | None = Field(None, description="Marketing flight number")
    origin: str | None = Field(None, description="Origin airport code")
    destination: str | None = Field(None, description="Destination airport code")
    scheduled_departure: datetime | None = Field(None, description="Scheduled departure")
    actual_departure: datetime | None = Field(None, description="Actual departure, if departed")
    status: FlightStatus | None = Field(None, description="Flight status")
    delay_minutes: int | None = Field(None, description="Departure delay in minutes (0 if on time)")
    arrival_delay_minutes: int | None = Field(None, description="Arrival delay in minutes")
    cause: DelayCause | None = Field(None, description="Whether the delay/cancellation was airline-controllable")
    cause_detail: str | None = Field(None, description="Specific cause: weather, atc, crew, maintenance, it")
    is_cancelled_by_airline: bool | None = Field(None, description="Airline cancelled the flight")
    schedule_change_minutes: int | None = Field(None, description="Minutes the schedule moved after purchase")
    is_significant_change: bool | None = Field(
        None, description="Change meets the DOT 'significant change' definition (e.g. >= 3h domestic)"
    )
    customer_accepted_alternative: bool | None = Field(
        None, description="Customer accepted rebooking or alternative transportation"
    )
    overnight_required: bool | None = Field(None, description="Disruption required an overnight stay")
    is_domestic: bool | None = Field(None, description="Both airports in the US")


class Bag(OntologyModel):
    bag_tag: str | None = Field(None, description="Bag tag number")
    status: BagStatus | None = Field(None, description="Bag status")
    checked: bool | None = Field(None, description="Bag was checked (not carry-on)")
    fee_paid: Decimal | None = Field(None, description="Checked-bag fee paid for this bag")
    minutes_to_carousel: int | None = Field(
        None, description="Minutes from aircraft door open to bag on carousel (bag service guarantee)"
    )
    guarantee_claim_filed_within_hours: int | None = Field(
        None, description="Hours after arrival at which a bag-guarantee claim was filed"
    )
    report_filed: bool | None = Field(None, description="A delayed/damaged bag report is on file with the airline")
    report_filed_within_hours: int | None = Field(None, description="Hours after arrival the report was filed")
    delay_days: int | None = Field(None, description="Days the bag was delayed")
    damage_type: str | None = Field(
        None, description="Damage type: structural, wheel, handle, zipper, cosmetic, contents, none"
    )
    declared_value: Decimal | None = Field(None, description="Declared excess value, if any")
    interim_expenses: Decimal | None = Field(None, description="Interim expenses claimed while delayed")


class Booking(OntologyModel):
    pnr: str | None = Field(None, description="Confirmation code")
    ticket_number: str | None = Field(None, description="E-ticket number")
    fare_class: str | None = Field(None, description="Fare family: saver, main, first, premium")
    is_refundable_fare: bool | None = Field(None, description="Fare is refundable to original form of payment")
    purchased_at: datetime | None = Field(None, description="When the ticket was purchased")
    hours_since_purchase: int | None = Field(None, description="Hours between purchase and the request")
    days_until_departure_at_purchase: int | None = Field(None, description="Days between purchase and first departure")
    hours_until_departure: int | None = Field(None, description="Hours between the request and first departure")
    total_paid: Decimal | None = Field(None, description="Total fare paid including taxes and fees")
    currency: str | None = Field(None, description="Currency code")
    payment_method: PaymentMethod | None = Field(None, description="Original form of payment")
    paid_with_credit_amount: Decimal | None = Field(None, description="Portion paid with travel credit")
    ancillary_seat_fee: Decimal | None = Field(None, description="Paid seat selection fee")
    ancillary_bag_fee: Decimal | None = Field(None, description="Paid checked-bag fees")
    ancillary_other_fee: Decimal | None = Field(None, description="Other paid optional services")
    status: str | None = Field(None, description="active, flown, cancelled_by_customer, cancelled_by_airline, no_show")
    customer_cancelled: bool | None = Field(None, description="Customer initiated the cancellation")
    travel_completed: bool | None = Field(None, description="All segments flown")
    loyalty_number: str | None = Field(None, description="Loyalty account number (masked)")
    flights: list[Flight] = Field(default_factory=list, description="Segments")
    bags: list[Bag] = Field(default_factory=list, description="Checked bags")


class Claim(OntologyModel):
    """What the customer is asking for and what they have provided.

    These are facts extracted from the conversation rather than from merchant systems.
    """

    reason: str | None = Field(
        None,
        description="Primary reason: damaged_on_arrival, defective, wrong_item, missing_item, change_of_mind, "
        "lifetime_guarantee, backorder, order_change, cancel_order, flight_cancelled, flight_delayed, "
        "schedule_change, refund_request, credit_request, bag_delayed, bag_damaged, bag_lost, "
        "bag_guarantee, seat_refund, ancillary_refund, loyalty_points, other",
    )
    requested_remedy: Remedy | None = Field(None, description="Remedy the customer asked for")
    requested_amount: Decimal | None = Field(None, description="Amount the customer asked for, if stated")
    has_photos: bool | None = Field(None, description="Customer has provided photos of damage/defect")
    has_receipt: bool | None = Field(None, description="Customer has proof of purchase")
    reported_within_days: int | None = Field(None, description="Days after delivery/arrival the issue was reported")
    item_unopened: bool | None = Field(None, description="Item is in original unopened packaging")
    item_used: bool | None = Field(None, description="Item has been used")
    wants_credit_instead_of_refund: bool | None = Field(None, description="Customer prefers credit")
    description: str | None = Field(None, description="Short masked description of the issue")
    intents: list[str] | None = Field(
        None,
        description="Ordered customer intents: refund, cancel, rebook, replacement, return, status, timeline, "
        "compensation, complaint, bag, other",
    )
    timeline_ask: bool | None = Field(None, description="Customer asked when the money or item will arrive")
    reference_kind: str | None = Field(None, description="confirmation_code | order_number | none")


class Party(OntologyModel):
    """Who is travelling or ordering, when the customer states it (design v2 §2)."""

    pax_count: int | None = Field(None, description="Number of passengers or people on the booking")
    pax_types: list[str] | None = Field(None, description="adult | child | infant, one per passenger")


class StaffAction(OntologyModel):
    seq: int = Field(..., description="Order of the action in the thread")
    action: str = Field(..., description="Normalised action verb, e.g. request_photos, issue_refund, send_label")
    detail: str | None = Field(None, description="Masked free-text detail")
    amount: Decimal | None = Field(None, description="Amount if the action moved money")
    at: datetime | None = Field(None, description="When the action happened")


class CaseOutcome(OntologyModel):
    decision: Decision | None = Field(None, description="Decision reached")
    remedy: Remedy | None = Field(None, description="Remedy issued")
    amount: Decimal | None = Field(None, description="Amount issued")
    fault: Fault | None = Field(None, description="Fault assignment")


class Case(OntologyModel):
    """A mined or live case: the unit of learning and resolution."""

    case_id: str | None = Field(None, description="Umpyr case identifier")
    ticket_id: str | None = Field(None, description="Source ticket identifier")
    category: str | None = Field(None, description="Taxonomy category key")
    status: str | None = Field(None, description="open, resolved, escalated")
    summary: str | None = Field(None, description="One-line masked summary")
    staff_actions: list[StaffAction] = Field(default_factory=list, description="Ordered staff actions")
    outcome: CaseOutcome | None = Field(None, description="Outcome of the case")
    opened_at: datetime | None = Field(None, description="When the case opened")
    resolved_at: datetime | None = Field(None, description="When the case resolved")
    hours_to_resolve: float | None = Field(None, description="Hours from open to resolution")
    is_drift: bool | None = Field(None, description="Staff outcome contradicts compiled policy")
    drift_reason: str | None = Field(None, description="Why it is drift")
    is_synthetic: bool = Field(True, description="Synthetic data flag (POC data is synthetic)")


class Facts(OntologyModel):
    """The fact bundle a rule evaluates over. Rules use paths like `order.total` or `claim.has_photos`."""

    customer: Customer = Field(default_factory=Customer)
    order: Order = Field(default_factory=Order)
    item: Item = Field(default_factory=Item, description="The item the claim is about")
    shipment: Shipment = Field(default_factory=Shipment, description="The shipment the claim is about")
    booking: Booking = Field(default_factory=Booking)
    flight: Flight = Field(default_factory=Flight, description="The segment the claim is about")
    bag: Bag = Field(default_factory=Bag, description="The bag the claim is about")
    claim: Claim = Field(default_factory=Claim)
    case: Case = Field(default_factory=Case)
    party: Party = Field(default_factory=Party)

    def to_facts_dict(self) -> dict[str, Any]:
        """Nested dict suitable for JSON Logic `var` lookups. None values are omitted so that
        `missing` checks work."""

        def strip(obj: Any) -> Any:
            if isinstance(obj, dict):
                return {k: strip(v) for k, v in obj.items() if v is not None}
            if isinstance(obj, list):
                return [strip(v) for v in obj]
            return obj

        return strip(self.model_dump(mode="python"))


class TicketMessage(OntologyModel):
    role: str = Field(..., description="customer or staff")
    at: datetime | None = Field(None, description="Timestamp")
    text: str = Field(..., description="Masked message text")


class Ticket(OntologyModel):
    """Canonical ticket schema. Matches samples/ticket_template.csv."""

    ticket_id: str
    created_at: datetime | None = None
    channel: Channel | None = None
    subject: str | None = None
    thread: list[TicketMessage] = Field(default_factory=list)
    agent_actions: list[str] = Field(default_factory=list)
    resolution: str | None = None
    amount: Decimal | None = None
    order_id: str | None = None
    is_synthetic: bool = True
