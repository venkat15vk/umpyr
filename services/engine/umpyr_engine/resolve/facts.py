"""Fact assembly shared by mining (historical) and the resolve runtime (live).

build_facts(claim, airline, record, at, kind) -> ontology Facts dict with derived date fields computed as of `at`.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

ORDER_RE = re.compile(r"\b(R-\d{5,7})\b", re.I)
PNR_RE = re.compile(r"\b([A-Z]{6})\b")
ORDER_WORDS_RE = re.compile(
    r"(?:order|confirmation|booking|record locator|pnr|reference)\s*(?:#|number|no\.?|code|:)?\s*([A-Z0-9-]{5,12})",
    re.I,
)


def parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def find_reference(text: str, kind: str) -> str | None:
    """Pull an order number (R-xxxxxx) or a 6-letter confirmation code out of free text."""
    if kind == "booking":
        m = ORDER_WORDS_RE.search(text)
        if m and PNR_RE.fullmatch(m.group(1).upper()):
            return m.group(1).upper()
        for cand in PNR_RE.findall(text.upper()):
            if cand not in {
                "PLEASE",
                "THANKS",
                "FLIGHT",
                "REFUND",
                "CREDIT",
                "TICKET",
                "ALASKA",
                "SEATTLE",
                "BOSTON",
                "DENVER",
            }:
                return cand
        return None
    m = ORDER_RE.search(text)
    if m:
        return m.group(1).upper()
    m = ORDER_WORDS_RE.search(text)
    return m.group(1).upper() if m else None


def _item_for(items: list[dict], hint: str | None) -> dict | None:
    if not items:
        return None
    h = (hint or "").lower()
    if h:
        for it in items:
            name = str(it.get("name", "")).lower()
            if str(it.get("sku", "")).lower() in h or (name and any(w in h for w in name.split()[:3] if len(w) > 3)):
                return it
    return items[0]


def build_facts(
    claim: dict[str, Any],
    airline: dict[str, Any] | None,
    record: dict[str, Any] | None,
    at: datetime,
    kind: str,
    item_hint: str | None = None,
) -> dict[str, Any]:
    claim = {k: v for k, v in (claim or {}).items() if v is not None}
    facts: dict[str, Any] = {"claim": claim}
    if not record:
        return facts
    if kind == "booking":
        booking = {
            k: v for k, v in record.items() if k not in ("flights", "bags", "customer", "order_id") and v is not None
        }
        facts["booking"] = booking
        flights = record.get("flights") or []
        bags = record.get("bags") or []
        if flights:
            facts["flight"] = {k: v for k, v in flights[0].items() if v is not None}
        if bags:
            facts["bag"] = {k: v for k, v in bags[0].items() if v is not None}
        air = {k: v for k, v in (airline or {}).items() if v is not None}
        f = facts.setdefault("flight", {})
        for k in (
            "delay_minutes",
            "cause",
            "is_cancelled_by_airline",
            "customer_accepted_alternative",
            "overnight_required",
        ):
            if k in air:
                f[k] = air[k]
        b = facts.setdefault("bag", {})
        for src, dst in (
            ("bag_minutes_to_carousel", "minutes_to_carousel"),
            ("bag_report_filed", "report_filed"),
            ("bag_report_filed_within_hours", "report_filed_within_hours"),
            ("bag_delay_days", "delay_days"),
            ("bag_damage_type", "damage_type"),
            ("interim_expenses", "interim_expenses"),
        ):
            if src in air:
                b[dst] = air[src]
        if "customer_cancelled" in air:
            booking["customer_cancelled"] = air["customer_cancelled"]
        purchased = parse_dt(record.get("purchased_at"))
        if purchased:
            booking["hours_since_purchase"] = int((at - purchased).total_seconds() // 3600)
        if flights:
            dep = parse_dt(flights[0].get("scheduled_departure"))
            if dep:
                booking["hours_until_departure"] = int((dep - at).total_seconds() // 3600)
        if record.get("customer"):
            facts["customer"] = {
                k: v for k, v in record["customer"].items() if k in ("customer_id", "loyalty_member", "loyalty_tier")
            }
        return facts
    facts["order"] = {k: v for k, v in record.items() if k not in ("items", "shipments", "customer") and v is not None}
    placed = parse_dt(record.get("placed_at"))
    if placed:
        facts["order"]["days_since_order"] = max(0, (at - placed).days)
    item = _item_for(record.get("items") or [], item_hint)
    if item:
        facts["item"] = {k: v for k, v in item.items() if v is not None}
        reason = claim.get("reason")
        if reason == "damaged_on_arrival":
            facts["item"]["condition"] = "damaged"
        elif reason in ("defective", "lifetime_guarantee"):
            facts["item"]["condition"] = "defective"
        elif claim.get("item_used"):
            facts["item"]["condition"] = "used"
        elif claim.get("item_unopened"):
            facts["item"]["condition"] = "new_unopened"
        elif claim.get("item_unopened") is False:
            facts["item"]["condition"] = "opened_unused"
    ships = record.get("shipments") or []
    if ships:
        facts["shipment"] = {k: v for k, v in ships[0].items() if v is not None}
        delivered = parse_dt(ships[0].get("delivered_at"))
        if delivered:
            facts["shipment"]["days_since_delivery"] = max(0, (at - delivered).days)
            claim.setdefault("reported_within_days", max(0, (at - delivered).days))
    if record.get("customer"):
        facts["customer"] = {
            k: v for k, v in record["customer"].items() if k in ("customer_id", "loyalty_member", "loyalty_tier")
        }
    return facts
