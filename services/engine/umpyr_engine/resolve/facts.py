"""Fact assembly shared by mining (historical) and the resolve runtime (live).

build_facts(claim, airline, record, at, kind) -> ontology Facts dict with derived date fields computed as of `at`.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

ORDER_RE = re.compile(r"\b(R-\d{5,7})\b", re.I)
PNR_RE = re.compile(r"\b([A-Z0-9]{6})\b")  # bare tokens must be upper-case in the source text
ORDER_WORDS_RE = re.compile(
    r"(?:order|confirmation|booking|record locator|pnr|reference)(?:\s*(?:#|number|no\.?|code|locator|is|:))*\s*([A-Z0-9-]{5,12})",
    re.I,
)
STOP_TOKENS = {
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
    "CANCEL",
    "CHANGE",
    "REBOOK",
    "HOTELS",
    "BOOKED",
    "NUMBER",
    "ONLINE",
    "MOBILE",
    "STATUS",
    "DELAYED",
    "BEFORE",
    "ARRIVED",
    "BROKEN",
    "EMAIL",
    "PHONE",
    "AGAIN",
    "TODAY",
    "ISSUE",
}


def _looks_like_code(token: str, source: str) -> bool:
    """A booking code is 5-8 alphanumerics with a letter; a dictionary word only counts if typed in upper case."""
    t = token.upper()
    if t in STOP_TOKENS or not re.fullmatch(r"(?=.*[A-Z])[A-Z0-9]{5,8}", t):
        return False
    return any(ch.isdigit() for ch in t) or token.isupper() or len(t) == 6


def find_reference(text: str, kind: str) -> str | None:
    """Pull an order number (R-xxxxxx) or a confirmation code out of free text."""
    if kind == "booking":
        m = ORDER_WORDS_RE.search(text)
        if m and _looks_like_code(m.group(1), text):
            return m.group(1).upper()
        for cand in PNR_RE.findall(text):  # not upper-cased: "booked" must not become a code
            if cand not in STOP_TOKENS and any(ch.isalpha() for ch in cand):
                return cand
        return None
    m = ORDER_RE.search(text)
    if m:
        return m.group(1).upper()
    m = ORDER_WORDS_RE.search(text)
    if m and any(ch.isdigit() for ch in m.group(1)):
        return m.group(1).upper()
    return None


def parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


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


BAG_STATUS_BY_REASON = {"bag_damaged": "damaged", "bag_delayed": "delayed", "bag_lost": "lost"}


DAMAGE_TYPES = ("structural", "wheel", "handle", "zipper", "cosmetic", "contents", "none")
_DAMAGE_WORDS = {
    "wheel": ("wheel", "caster"),
    "handle": ("handle", "strap", "grip"),
    "zipper": ("zip",),
    "contents": ("content", "item inside", "missing item", "stolen"),
    "cosmetic": ("scratch", "scuff", "dent", "cosmetic", "stain"),
    "structural": ("crack", "broken", "shell", "frame", "torn", "rip", "hole", "crush", "split", "structural"),
}


def normalise_damage_type(value: Any) -> str | None:
    """Map free text ("missing wheel", "cracked shell") onto the ontology's damage vocabulary."""
    if value is None:
        return None
    v = str(value).strip().lower()
    if v in DAMAGE_TYPES:
        return v
    if v in ("", "unknown"):
        return None
    for kind, words in _DAMAGE_WORDS.items():
        if any(w in v for w in words):
            return kind
    return "structural"


CAUSE_WORDS = {
    "controllable": (
        "crew",
        "mechanical",
        "maintenance",
        "staff",
        "aircraft",
        "plane problem",
        "it outage",
        "system outage",
        "overbook",
        "catering",
        "cleaning",
        "fueling",
        "within their control",
        "airline's fault",
        "airline fault",
    ),
    "uncontrollable": (
        "weather",
        "storm",
        "snow",
        "fog",
        "wind",
        "hurricane",
        "thunder",
        "air traffic",
        "atc",
        "tower",
        "security",
        "runway closure",
        "volcan",
        "strike",
        "outside their control",
    ),
}


def grounded_cause(extracted_cause: Any, customer_text: str) -> str | None:
    """Keep a delay cause only when the customer's own words name it; the model must not guess (D52)."""
    if extracted_cause not in ("controllable", "uncontrollable"):
        return None
    t = (customer_text or "").lower()
    return extracted_cause if any(w in t for w in CAUSE_WORDS[extracted_cause]) else None


def derive_bag_facts(claim: dict[str, Any] | None, airline: dict[str, Any] | None) -> dict[str, Any]:
    """Bag facts implied by what the customer said, so a bag claim never re-asks "was it checked / what happened".
    A bag complaint is about a checked bag; a missing wheel is damage; days late is a delay."""
    claim = claim or {}
    air = {k: v for k, v in (airline or {}).items() if v is not None}
    out: dict[str, Any] = {}
    reason = claim.get("reason") or ""
    if reason in BAG_STATUS_BY_REASON:
        out["status"] = BAG_STATUS_BY_REASON[reason]
        out["checked"] = True
    elif reason == "bag_guarantee":
        out["checked"] = True
    dmg = normalise_damage_type(air.get("bag_damage_type"))
    if dmg and dmg != "none":
        out["status"] = "damaged"
        out["checked"] = True
        out["damage_type"] = dmg
    if (air.get("bag_delay_days") or 0) > 0 and "status" not in out:
        out["status"] = "delayed"
        out["checked"] = True
    if air.get("bag_status") in ("delivered", "delayed", "damaged", "lost"):
        out["status"] = air["bag_status"]
        out.setdefault("checked", True)
    if air.get("bag_checked") is not None:
        out["checked"] = bool(air["bag_checked"])
    return out


def build_facts(
    claim: dict[str, Any],
    airline: dict[str, Any] | None,
    record: dict[str, Any] | None,
    at: datetime,
    kind: str,
    item_hint: str | None = None,
) -> dict[str, Any]:
    claim = {k: v for k, v in (claim or {}).items() if v is not None}
    if claim.get("item_unopened") is True and claim.get("item_used") is None:
        claim["item_used"] = False  # still sealed in the box means never used; do not ask
    facts: dict[str, Any] = {"claim": claim}
    if not record:
        derived = derive_bag_facts(claim, airline)
        if derived:
            facts["bag"] = derived
        return facts
    claim["has_receipt"] = True  # the merchant's own record is proof of purchase; a lost receipt changes nothing
    if kind == "booking":
        booking = {
            k: v
            for k, v in record.items()
            if k not in ("flights", "bags", "customer", "order_id", "stand_in", "stand_in_template") and v is not None
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
        for k, v in derive_bag_facts(claim, air).items():  # what the customer said about the bag wins over a template
            b[k] = v
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
    facts["order"] = {
        k: v
        for k, v in record.items()
        if k not in ("items", "shipments", "customer", "stand_in", "stand_in_template") and v is not None
    }
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
