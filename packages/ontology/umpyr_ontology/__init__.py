"""Umpyr ontology.

Every procedure and rule in Umpyr references only the fields defined here.
Merchant data is mapped *into* these models by adapters; vendor-specific
fields never appear in rules.
"""

from .catalog import FIELD_CATALOG, FieldSpec, field_paths, validate_path
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
    Tier,
)
from .models import (
    Bag,
    Booking,
    Case,
    CaseOutcome,
    Claim,
    Customer,
    Facts,
    Flight,
    Item,
    Order,
    Shipment,
    StaffAction,
    Ticket,
    TicketMessage,
)

__all__ = [
    "FIELD_CATALOG",
    "FieldSpec",
    "field_paths",
    "validate_path",
    "BagStatus",
    "Channel",
    "Decision",
    "DelayCause",
    "Fault",
    "FlightStatus",
    "ItemCondition",
    "PaymentMethod",
    "Remedy",
    "ShipmentStatus",
    "Tier",
    "Bag",
    "Booking",
    "Case",
    "CaseOutcome",
    "Claim",
    "Customer",
    "Facts",
    "Flight",
    "Item",
    "Order",
    "Shipment",
    "StaffAction",
    "Ticket",
    "TicketMessage",
]
