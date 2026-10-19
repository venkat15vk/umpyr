"""Rockler synthetic orders and tickets (~300), generated from the compiled rule pack + hand-written scenario templates."""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from umpyr_rules import RulePack, evaluate

from .core import NOW, Person, Scenario, StaffStep, iso, make_person, money

# ---------------------------------------------------------------- catalog
# sku, name, category, brand, price, flags
CATALOG: list[dict[str, Any]] = [
    {
        "sku": "RB-1001",
        "name": 'Rockler 1/2" Flush Trim Router Bit',
        "category": "router_bit",
        "brand": "Rockler",
        "price": 29.99,
        "lifetime": True,
    },
    {
        "sku": "RB-1017",
        "name": "Rockler 45° Chamfer Router Bit",
        "category": "router_bit",
        "brand": "Rockler",
        "price": 34.99,
        "lifetime": True,
    },
    {
        "sku": "RB-1042",
        "name": "Rockler Roundover Bit Set (4 pc)",
        "category": "router_bit",
        "brand": "Rockler",
        "price": 89.99,
        "lifetime": True,
    },
    {
        "sku": "DS-2201",
        "name": 'Rockler TopSlide 18" Drawer Slides (pair)',
        "category": "drawer_slide",
        "brand": "Rockler",
        "price": 24.99,
        "lifetime": True,
    },
    {
        "sku": "DS-2210",
        "name": 'Rockler Centerline 22" Soft-Close Drawer Slides (pair)',
        "category": "drawer_slide",
        "brand": "Rockler",
        "price": 39.99,
        "lifetime": True,
    },
    {
        "sku": "DS-2290",
        "name": 'Blum 21" Undermount Slides (pair)',
        "category": "drawer_slide",
        "brand": "Blum",
        "price": 54.99,
    },
    {
        "sku": "CL-3001",
        "name": 'Rockler Sure-Foot 24" Bar Clamp',
        "category": "hardware",
        "brand": "Rockler",
        "price": 32.99,
    },
    {
        "sku": "CL-3010",
        "name": 'Bessey 50" Parallel Clamp (2 pack)',
        "category": "hardware",
        "brand": "Bessey",
        "price": 129.99,
    },
    {
        "sku": "HW-4001",
        "name": "Rockler Bench Dog Hold-Down",
        "category": "hardware",
        "brand": "Rockler",
        "price": 44.99,
    },
    {
        "sku": "HW-4015",
        "name": "Brusso Solid Brass Box Hinges (pair)",
        "category": "hardware",
        "brand": "Brusso",
        "price": 64.99,
    },
    {
        "sku": "HW-4030",
        "name": "Rockler Pocket-Hole Jig Kit",
        "category": "hardware",
        "brand": "Rockler",
        "price": 69.99,
    },
    {
        "sku": "HW-4044",
        "name": "Rockler Dust Right Separator",
        "category": "hardware",
        "brand": "Rockler",
        "price": 149.99,
    },
    {
        "sku": "PT-5001",
        "name": "DeWalt 20V Cordless Drill/Driver Kit",
        "category": "power_tool",
        "brand": "DeWalt",
        "price": 179.00,
        "power_tool": True,
    },
    {
        "sku": "PT-5008",
        "name": 'Makita 5" Random Orbit Sander',
        "category": "power_tool",
        "brand": "Makita",
        "price": 89.00,
        "power_tool": True,
    },
    {
        "sku": "PT-5020",
        "name": "Bosch Colt Palm Router",
        "category": "power_tool",
        "brand": "Bosch",
        "price": 129.00,
        "power_tool": True,
    },
    {
        "sku": "PT-5033",
        "name": "Festool Domino DF 500 Joiner",
        "category": "power_tool",
        "brand": "Festool",
        "price": 1099.00,
        "power_tool": True,
    },
    {
        "sku": "MC-6001",
        "name": 'SawStop 10" Contractor Table Saw',
        "category": "machinery",
        "brand": "SawStop",
        "price": 2399.00,
        "power_tool": True,
        "machinery": True,
        "freight": True,
    },
    {
        "sku": "MC-6012",
        "name": "Laguna Fusion F2 Table Saw",
        "category": "machinery",
        "brand": "Laguna",
        "price": 1899.00,
        "power_tool": True,
        "machinery": True,
        "freight": True,
    },
    {
        "sku": "MC-6020",
        "name": "Laguna 14|bx Bandsaw",
        "category": "machinery",
        "brand": "Laguna",
        "price": 1549.00,
        "power_tool": True,
        "machinery": True,
        "freight": True,
    },
    {
        "sku": "MC-6030",
        "name": 'Jet 8" Jointer',
        "category": "machinery",
        "brand": "Jet",
        "price": 1799.00,
        "power_tool": True,
        "machinery": True,
        "freight": True,
    },
    {
        "sku": "MC-6040",
        "name": 'Rikon 10" Bandsaw',
        "category": "machinery",
        "brand": "Rikon",
        "price": 449.00,
        "power_tool": True,
        "machinery": True,
    },
    {
        "sku": "LB-7001",
        "name": "Walnut Lumber Pack 4/4 (10 bf)",
        "category": "lumber",
        "brand": "Rockler",
        "price": 119.00,
    },
    {
        "sku": "LB-7010",
        "name": "Hard Maple Turning Blank 3x3x12",
        "category": "lumber",
        "brand": "Rockler",
        "price": 18.99,
    },
    {
        "sku": "FN-8001",
        "name": "General Finishes Arm-R-Seal Quart",
        "category": "finish",
        "brand": "General Finishes",
        "price": 26.99,
    },
    {"sku": "FN-8010", "name": "Osmo Polyx-Oil 750ml", "category": "finish", "brand": "Osmo", "price": 54.99},
    {
        "sku": "CR-9001",
        "name": "Rockler Router Table Fence (Clearance)",
        "category": "clearance",
        "brand": "Rockler",
        "price": 59.99,
        "final_sale": True,
    },
    {
        "sku": "CR-9010",
        "name": "Shop Apron, Canvas (Clearance)",
        "category": "clearance",
        "brand": "Rockler",
        "price": 14.99,
        "final_sale": True,
    },
    {
        "sku": "CR-9020",
        "name": "Workbench Caster Set (Clearance)",
        "category": "clearance",
        "brand": "Rockler",
        "price": 39.99,
        "final_sale": True,
    },
    {
        "sku": "PL-9501",
        "name": "Adirondack Chair Plan (Downloadable)",
        "category": "plan",
        "brand": "Rockler",
        "price": 9.99,
        "final_sale": True,
    },
]
SKU = {c["sku"]: c for c in CATALOG}


def shipping_charge(subtotal: float) -> float:
    table = [
        (15, 7.99),
        (25, 9.99),
        (50, 11.99),
        (75, 13.99),
        (100, 15.99),
        (125, 17.99),
        (150, 19.99),
        (175, 21.99),
        (200, 23.99),
    ]
    for cap, fee in table:
        if subtotal <= cap:
            return fee
    return 25.99


# ---------------------------------------------------------------- orders
def make_order(
    rng: random.Random, idx: int, person: Person, focus_sku: str, placed_at: datetime, *, backorder: bool = False
) -> dict:
    items = [SKU[focus_sku]]
    for _ in range(rng.choice([0, 0, 1, 1, 2])):
        extra = rng.choice([c for c in CATALOG if not c.get("machinery") and c["sku"] != focus_sku])
        if extra not in items:
            items.append(extra)
    lines = []
    for it in items:
        qty = 1 if it.get("power_tool") or it.get("machinery") else rng.choice([1, 1, 1, 2])
        lines.append(
            {
                "item_id": f"L{idx}-{len(lines) + 1}",
                "sku": it["sku"],
                "name": it["name"],
                "category": it["category"],
                "brand": it["brand"],
                "price": money(it["price"]),
                "quantity": qty,
                "line_total": money(it["price"] * qty),
                "is_final_sale": bool(it.get("final_sale")),
                "is_non_returnable": it["brand"] in ("SawStop", "Laguna") and bool(it.get("power_tool")),
                "is_power_tool": bool(it.get("power_tool")),
                "is_machinery": bool(it.get("machinery")),
                "is_custom": False,
                "has_lifetime_guarantee": bool(it.get("lifetime")),
                "freight_required": bool(it.get("freight")),
            }
        )
    subtotal = float(sum(line["line_total"] for line in lines))
    freight = any(line["freight_required"] for line in lines)
    shipping = 0.0 if subtotal >= 1000 and not freight else (149.0 if freight else shipping_charge(subtotal))
    tax = round(subtotal * 0.07, 2)
    rewards_discount = rng.choice([0, 0, 0, 0, 0, 10, 20, 25, 50]) if subtotal > 100 else 0
    payment = rng.choices(["card", "card", "card", "card", "paypal", "gift_card"], k=1)[0]
    total = round(subtotal + shipping + tax - rewards_discount, 2)
    shipped_at = None if backorder else placed_at + timedelta(days=rng.randint(1, 3), hours=rng.randint(0, 10))
    delivered_at = (
        None if backorder else shipped_at + timedelta(days=(rng.randint(5, 12) if freight else rng.randint(3, 7)))
    )
    carrier = "Estes Freight" if freight else rng.choice(["UPS Ground", "UPS SurePost", "USPS"])
    order = {
        "order_id": f"R-{100000 + idx}",
        "placed_at": iso(placed_at),
        "status": "backordered" if backorder else ("delivered" if delivered_at and delivered_at < NOW else "shipped"),
        "subtotal": money(subtotal),
        "shipping_total": money(shipping),
        "tax_total": money(tax),
        "total": money(total),
        "payment_method": payment,
        "rewards_discount": money(rewards_discount),
        "is_backordered": backorder,
        "backorder_eta_days": rng.randint(7, 45) if backorder else None,
        "has_shipped": shipped_at is not None,
        "items": lines,
        "shipments": []
        if backorder
        else [
            {
                "shipment_id": f"S-{idx}",
                "carrier": carrier,
                "method": "freight" if freight else "ground",
                "is_freight": freight,
                "shipped_at": iso(shipped_at),
                "delivered_at": iso(delivered_at),
                "status": "delivered",
                "signed_for": freight,
                "shipping_paid": money(shipping),
            }
        ],
        "customer": {
            "customer_id": person.customer_id,
            "name": person.name,
            "email": person.email,
            "phone": person.phone,
            "address": person.address,
            "loyalty_member": rng.random() < 0.3,
        },
    }
    return order


# ---------------------------------------------------------------- facts
def build_facts(order: dict, item: dict, claim: dict, at: datetime) -> dict[str, Any]:
    placed = datetime.fromisoformat(order["placed_at"].rstrip("Z"))
    ship = order["shipments"][0] if order["shipments"] else None
    facts: dict[str, Any] = {
        "customer": {
            "customer_id": order["customer"]["customer_id"],
            "loyalty_member": order["customer"]["loyalty_member"],
        },
        "order": {k: v for k, v in order.items() if k not in ("items", "shipments", "customer") and v is not None},
        "item": {k: v for k, v in item.items() if v is not None},
        "claim": {k: v for k, v in claim.items() if v is not None},
    }
    facts["order"]["days_since_order"] = max(0, (at - placed).days)
    if ship:
        delivered = datetime.fromisoformat(ship["delivered_at"].rstrip("Z"))
        facts["shipment"] = {k: v for k, v in ship.items() if v is not None}
        facts["shipment"]["days_since_delivery"] = max(0, (at - delivered).days)
    return facts


# ---------------------------------------------------------------- scenarios
CATEGORY_HOURS = {
    "damaged_on_arrival": (4, 36),
    "defective_item": (6, 48),
    "change_of_mind_return": (1, 20),
    "lifetime_guarantee": (12, 72),
    "backorder_status": (0.5, 8),
    "order_cancel_change": (0.5, 6),
}

MIX = [
    ("damaged_on_arrival", 70),
    ("defective_item", 30),
    ("change_of_mind_return", 90),
    ("lifetime_guarantee", 35),
    ("backorder_status", 35),
    ("order_cancel_change", 40),
]


def _pick(rng: random.Random, cat: str) -> dict:
    pools = {
        "damaged_on_arrival": [
            c
            for c in CATALOG
            if c["category"] in ("hardware", "machinery", "lumber", "finish", "power_tool", "router_bit")
        ],
        "defective_item": [
            c for c in CATALOG if c["category"] in ("hardware", "power_tool", "router_bit", "drawer_slide")
        ],
        "change_of_mind_return": CATALOG,
        "lifetime_guarantee": [c for c in CATALOG if c.get("lifetime")],
        "backorder_status": [c for c in CATALOG if not c.get("final_sale")],
        "order_cancel_change": [c for c in CATALOG if not c.get("final_sale")],
    }
    return rng.choice(pools[cat])


def _ruling_summary(r) -> dict[str, Any]:
    return {
        "decision": r.decision,
        "remedy": r.remedy,
        "amount": str(r.amount) if r.amount is not None else None,
        "fault": r.fault,
        "rule_id": r.winning_rule_id,
        "clause_id": r.winning_clause_id,
        "clause_ids": r.clause_ids,
        "missing_facts": r.missing_facts,
        "explanation": r.explanation,
    }


def generate(pack: RulePack, n: int = 300, seed: int = 7) -> tuple[list[dict], list[Scenario], list[str]]:
    rng = random.Random(seed)
    orders: list[dict] = []
    scenarios: list[Scenario] = []
    names: list[str] = []
    cats = [c for c, w in MIX for _ in range(w)]
    rng.shuffle(cats)
    cats = cats[:n]
    for i, cat in enumerate(cats):
        person = make_person(rng, i)
        names.append(person.name)
        product = _pick(rng, cat)
        # timing
        if cat == "lifetime_guarantee":
            placed = NOW - timedelta(days=rng.randint(200, 1800))
        elif cat == "change_of_mind_return":
            placed = NOW - timedelta(days=rng.choice([rng.randint(8, 80)] * 4 + [rng.randint(95, 160)]))
        elif cat in ("backorder_status", "order_cancel_change"):
            placed = NOW - timedelta(days=rng.randint(1, 20))
        else:
            placed = NOW - timedelta(days=rng.randint(6, 40))
        backorder = cat == "backorder_status" or (cat == "order_cancel_change" and rng.random() < 0.2)
        order = make_order(rng, i, person, product["sku"], placed, backorder=backorder)
        orders.append(order)
        line = next(li for li in order["items"] if li["sku"] == product["sku"])
        item = dict(line)
        sc = _scenario(rng, cat, order, item, person, pack, i)
        scenarios.append(sc)
    return orders, scenarios, names


def _scenario(
    rng: random.Random, cat: str, order: dict, item: dict, person: Person, pack: RulePack, i: int
) -> Scenario:
    placed = datetime.fromisoformat(order["placed_at"].rstrip("Z"))
    delivered = (
        datetime.fromisoformat(order["shipments"][0]["delivered_at"].rstrip("Z")) if order["shipments"] else None
    )
    ticket_id = f"RK-{20000 + i}"
    claim: dict[str, Any] = {}
    steps: list[StaffStep] = []
    brief = asks = ""
    at = NOW - timedelta(days=rng.randint(0, 3))

    if cat in ("damaged_on_arrival", "defective_item"):
        reason = "damaged_on_arrival" if cat == "damaged_on_arrival" else "defective"
        at = (delivered or placed) + timedelta(days=rng.randint(0, 9), hours=rng.randint(1, 20))
        has_photos_initially = rng.random() < 0.55
        item["condition"] = "damaged" if reason == "damaged_on_arrival" else "defective"
        claim = {
            "reason": reason,
            "has_photos": True,
            "reported_within_days": max(0, (at - (delivered or placed)).days),
            "requested_remedy": rng.choice(["replacement", "replacement", "refund"]),
        }
        if item.get("freight_required"):
            brief = (
                f"{item['name']} arrived by freight with visible damage (crushed crate / bent fence / dented table). "
                + (
                    "Damage was noted on the delivery receipt."
                    if rng.random() < 0.6
                    else "Driver left before they could inspect it."
                )
            )
        elif reason == "damaged_on_arrival":
            brief = f"{item['name']} arrived damaged: " + rng.choice(
                [
                    "box was crushed and the part is cracked",
                    "carbide edge is chipped",
                    "finish can leaked in the box",
                    "pieces broken off in transit",
                ]
            )
        else:
            brief = f"{item['name']} is defective: " + rng.choice(
                [
                    "stopped working after two uses",
                    "won't hold adjustment",
                    "motor makes a grinding noise",
                    "bit wobbles in the collet",
                ]
            )
        asks = "wants a replacement" if claim["requested_remedy"] == "replacement" else "wants a refund"
        if not has_photos_initially:
            steps.append(StaffStep("request_photos", "asked for photos of the damage and packaging"))
        steps.append(StaffStep("review_photos", "photos reviewed, damage confirmed"))
        claim["description"] = brief
        facts = build_facts(order, item, claim, at)
        ruling = evaluate(pack, facts)
    elif cat == "change_of_mind_return":
        at = placed + timedelta(days=min((NOW - placed).days, rng.randint(5, max(6, (NOW - placed).days))))
        unopened = rng.random() < 0.7
        used = (not unopened) and rng.random() < 0.5
        item["condition"] = "new_unopened" if unopened else ("used" if used else "opened_unused")
        claim = {
            "reason": "change_of_mind",
            "has_receipt": rng.random() < 0.9,
            "item_unopened": unopened,
            "item_used": used,
            "requested_remedy": "refund",
        }
        brief = (
            f"Wants to return {item['name']}: "
            + rng.choice(
                [
                    "bought the wrong size",
                    "found it cheaper elsewhere",
                    "project got cancelled",
                    "ordered two by mistake",
                    "changed plans",
                ]
            )
            + (
                " Item is still sealed."
                if unopened
                else (" Item has been used a few times." if used else " Opened the box but never used it.")
            )
        )
        asks = "wants a refund to the original payment"
        steps.append(StaffStep("verify_order", "order and purchase date verified"))
        claim["description"] = brief
        facts = build_facts(order, item, claim, at)
        ruling = evaluate(pack, facts)
    elif cat == "lifetime_guarantee":
        item["condition"] = "defective"
        claim = {
            "reason": "lifetime_guarantee",
            "has_receipt": rng.random() < 0.4,
            "requested_remedy": "replacement",
            "item_used": True,
        }
        brief = (
            f"{item['name']} failed after {rng.randint(1, 4)} years of use: "
            + rng.choice(
                [
                    "carbide tip snapped",
                    "bearing seized",
                    "slide no longer closes",
                    "ball bearings fell out",
                    "bit burns every cut now",
                ]
            )
            + (" Customer no longer has the receipt." if not claim["has_receipt"] else "")
        )
        asks = "asks whether the lifetime guarantee covers it and wants a replacement"
        steps.append(StaffStep("verify_order", "confirmed Rockler-branded guaranteed product"))
        claim["description"] = brief
        facts = build_facts(order, item, claim, at)
        ruling = evaluate(pack, facts)
    elif cat == "backorder_status":
        at = placed + timedelta(days=rng.randint(3, 15))
        wants_cancel = rng.random() < 0.4
        claim = {
            "reason": "cancel_order" if wants_cancel else "backorder",
            "requested_remedy": "cancel_order" if wants_cancel else "none",
        }
        brief = f"{item['name']} shows as backordered since the order was placed; no update received." + (
            " Customer wants to cancel the backordered item."
            if wants_cancel
            else " Customer asks for an expected ship date."
        )
        asks = "wants to cancel and be refunded" if wants_cancel else "wants an ETA"
        steps.append(StaffStep("verify_order", "checked backorder status and ETA"))
        if not wants_cancel:
            steps.append(StaffStep("provide_eta", f"ETA about {order['backorder_eta_days']} days"))
        claim["description"] = brief
        facts = build_facts(order, item, claim, at)
        ruling = evaluate(pack, facts)
    else:  # order_cancel_change
        shipped = order["has_shipped"]
        at = placed + timedelta(hours=rng.randint(2, 70)) if not shipped else placed + timedelta(days=rng.randint(2, 6))
        if shipped and at < datetime.fromisoformat(order["shipments"][0]["shipped_at"].rstrip("Z")):
            at = datetime.fromisoformat(order["shipments"][0]["shipped_at"].rstrip("Z")) + timedelta(hours=5)
        change = rng.random() < 0.4
        claim = {
            "reason": "order_change" if change else "cancel_order",
            "requested_remedy": "none" if change else "cancel_order",
        }
        brief = (
            f"Wants to {'change the shipping address' if rng.random() < 0.5 else 'change the quantity'} on the order"
            if change
            else "Wants to cancel the order"
        ) + (" which has already shipped." if shipped else " placed recently.")
        asks = "wants the change made" if change else "wants the order cancelled and refunded"
        steps.append(StaffStep("verify_order", "checked fulfilment status"))
        claim["description"] = brief
        facts = build_facts(order, item, claim, at)
        ruling = evaluate(pack, facts)

    policy = _ruling_summary(ruling)
    staff_outcome, more_steps, drift, drift_reason = _staff_outcome(rng, cat, ruling, item, order, claim)
    steps.extend(more_steps)
    lo, hi = CATEGORY_HOURS[cat]
    hours = round(rng.uniform(lo, hi) * (1.6 if drift else 1.0), 1)
    return Scenario(
        ticket_id=ticket_id,
        category_key=cat,
        claim_reason=claim["reason"],
        person=person,
        order_id=order["order_id"],
        created_at=at,
        facts=facts,
        issue_brief=brief,
        customer_asks=asks,
        policy=policy,
        staff_outcome=staff_outcome,
        staff_steps=steps,
        hours_to_resolve=hours,
        is_drift=drift,
        drift_reason=drift_reason,
        channel=rng.choice(["email", "email", "email", "web_form", "chat"]),
    )


def _staff_outcome(
    rng: random.Random, cat: str, ruling, item: dict, order: dict, claim: dict
) -> tuple[dict, list[StaffStep], bool, str | None]:
    """Policy-consistent outcome 85% of the time; otherwise a deliberate, labelled drift."""
    line_total = Decimal(str(item["line_total"]))
    decision, remedy, amount, fault = ruling.decision, ruling.remedy, ruling.amount, ruling.fault
    steps: list[StaffStep] = []
    drift = rng.random() < 0.15

    def out(d, r, a, f, note=""):
        return {"decision": d, "remedy": r, "amount": str(a) if a is not None else None, "fault": f, "note": note}

    if decision == "approve" and remedy == "replacement":
        if drift and claim.get("reason") in ("damaged_on_arrival", "defective"):
            steps.append(StaffStep("issue_replacement", "replacement sent without photo evidence"))
            return (
                out("approve", "replacement", line_total, fault),
                [s for s in steps],
                True,
                "Replacement issued without photo evidence (policy practice requires photos).",
            )
        steps.append(StaffStep("issue_replacement", "replacement order created at no charge", line_total))
        if claim.get("reason") == "damaged_on_arrival" and item.get("freight_required"):
            steps.append(StaffStep("file_carrier_claim", "freight damage claim opened with carrier"))
        return out("approve", "replacement", line_total, fault), steps, False, None
    if decision == "approve" and remedy == "refund":
        if drift:
            steps.append(StaffStep("issue_store_credit", "store credit issued instead of refund", line_total))
            return (
                out("approve", "store_credit", line_total, fault),
                steps,
                True,
                "Store credit issued where policy entitles a refund to the original payment.",
            )
        steps.append(StaffStep("send_return_label", "return instructions sent; customer pays return shipping"))
        steps.append(StaffStep("issue_refund", "refund to original payment after receipt", amount))
        return out("approve", "refund", amount, fault), steps, False, None
    if decision == "approve" and remedy == "cancel_order":
        steps.append(StaffStep("cancel_order", "order cancelled and refunded", amount))
        return out("approve", "cancel_order", amount, fault), steps, False, None
    if decision == "approve" and remedy in (None, "none"):
        return out("approve", "none", None, fault, "informational reply"), steps, False, None
    if decision == "partial":
        steps.append(StaffStep("issue_partial_refund", "partial refund issued", amount))
        return out("partial", remedy, amount, fault), steps, False, None
    if decision == "deny":
        if drift:
            goodwill = rng.choice(["refund", "store_credit"])
            steps.append(
                StaffStep(
                    "issue_refund" if goodwill == "refund" else "issue_store_credit",
                    "goodwill exception granted",
                    line_total,
                )
            )
            return (
                out("approve", goodwill, line_total, "merchant"),
                steps,
                True,
                f"Goodwill {goodwill} granted although policy denies ({ruling.winning_rule_id}).",
            )
        steps.append(StaffStep("check_policy", "policy checked"))
        steps.append(StaffStep("deny_request", ruling.explanation[:120]))
        return out("deny", "none", None, fault), steps, False, None
    if decision == "needs_approval":
        steps.append(StaffStep("escalate_supervisor", "sent for authorization"))
        if remedy == "return_label":  # power tool return with restocking fee
            fee_pct = Decimal("0.15")
            if drift:
                steps.append(StaffStep("issue_refund", "full refund, no restocking fee", line_total))
                return (
                    out("approve", "refund", line_total, "customer"),
                    steps,
                    True,
                    "Power tool return refunded in full without the restocking fee or authorization step.",
                )
            steps.append(StaffStep("authorize_return", "return authorized with 15% restocking fee"))
            steps.append(
                StaffStep("issue_partial_refund", "refund less restocking fee", money(line_total * (1 - fee_pct)))
            )
            return out("partial", "partial_refund", money(line_total * (1 - fee_pct)), "customer"), steps, False, None
        if remedy == "partial_refund":  # rewards card deduction
            if drift:
                steps.append(StaffStep("issue_refund", "full refund without rewards card deduction", line_total))
                return (
                    out("approve", "refund", line_total, "customer"),
                    steps,
                    True,
                    "Rewards Card value not deducted from the refund.",
                )
            steps.append(StaffStep("issue_partial_refund", "refund less redeemed rewards card", amount))
            return out("partial", "partial_refund", amount, "customer"), steps, False, None
        steps.append(StaffStep("authorize_return", "approved"))
        return out("approve", remedy, amount, fault), steps, False, None
    if decision == "needs_fact":
        # synthetic threads always end with the fact supplied; only the photo case reaches here
        steps.append(StaffStep("request_photos", "asked for photos"))
        steps.append(StaffStep("issue_replacement", "replacement after photos", line_total))
        return out("approve", "replacement", line_total, "carrier"), steps, False, None
    # escalate: a human decides
    steps.append(StaffStep("escalate_supervisor", "no matching policy; supervisor decided"))
    if claim.get("reason") in ("cancel_order", "order_change"):
        steps.append(StaffStep("deny_request", "too late to change; return under returns policy"))
        return out("deny", "none", None, "customer", "human decision"), steps, False, None
    steps.append(StaffStep("provide_eta", "explained status"))
    return out("approve", "none", None, None, "human decision"), steps, False, None


# ---------------------------------------------------------------- turn plans for the writer
AGENTS = ["Jordan", "Sam", "Taylor", "Casey", "Morgan", "Riley", "Avery", "Quinn"]


def turn_plan(rng: random.Random, sc: Scenario, order: dict, item: dict) -> dict[str, Any]:
    turns: list[dict[str, str]] = [
        {
            "role": "customer",
            "purpose": f"Describe the issue: {sc.issue_brief} Mention order {order['order_id']}. {sc.customer_asks}.",
        }
    ]
    actions = [s.action for s in sc.staff_steps]
    if "request_photos" in actions:
        turns.append(
            {"role": "staff", "purpose": "Apologise, confirm the order, and ask for photos of the item and packaging."}
        )
        turns.append(
            {"role": "customer", "purpose": "Send the photos (describe them) and ask how soon it can be resolved."}
        )
    final = sc.staff_outcome
    outcome_text = {
        (
            "approve",
            "replacement",
        ): "Confirm a free replacement is on its way and that the damaged item need not be returned.",
        (
            "approve",
            "refund",
        ): f"Explain how to return the item (customer pays return shipping) and that a refund of ${final['amount']} to the original payment follows receipt.",
        ("approve", "store_credit"): f"Offer a store credit of ${final['amount']} and explain how to return the item.",
        ("approve", "cancel_order"): f"Confirm the order is cancelled and ${final['amount']} will be refunded.",
        (
            "approve",
            "none",
        ): "Answer the question with the status/ETA and offer to cancel before shipment if preferred.",
        ("partial", "partial_refund"): f"Explain the deduction and confirm a refund of ${final['amount']}.",
        (
            "deny",
            "none",
        ): "Politely decline, state the policy reason in plain words, and offer any alternative that exists.",
    }.get(
        (final["decision"], final["remedy"]),
        f"Communicate the outcome: {final['decision']} / {final['remedy']} ({final.get('note', '')}).",
    )
    if "escalate_supervisor" in actions and "request_photos" not in actions:
        turns.append(
            {"role": "staff", "purpose": "Acknowledge, say it has been sent for authorization, and set expectations."}
        )
        turns.append({"role": "staff", "purpose": outcome_text})
    else:
        turns.append({"role": "staff", "purpose": outcome_text})
    if rng.random() < 0.5:
        turns.append(
            {
                "role": "customer",
                "purpose": rng.choice(
                    [
                        "Thank them briefly.",
                        "Say that works and ask a short follow-up (timing or tracking).",
                        "Express mild frustration but accept.",
                    ]
                ),
            }
        )
        if turns[-1]["purpose"] != "Thank them briefly.":
            turns.append({"role": "staff", "purpose": "Answer the follow-up briefly and close warmly."})
    return {
        "merchant": "Rockler Woodworking and Hardware (woodworking tools, hardware and lumber retailer)",
        "agent_name": rng.choice(AGENTS),
        "customer": {
            "name": sc.person.name,
            "email": sc.person.email,
            "phone": sc.person.phone,
            "address": sc.person.address,
        },
        "order": {
            "order_id": order["order_id"],
            "placed_on": order["placed_at"][:10],
            "items": [f"{li['quantity']} x {li['name']} (${li['line_total']})" for li in order["items"]],
            "total": str(order["total"]),
            "payment": order["payment_method"],
            "shipping": order["shipments"][0]["carrier"] if order["shipments"] else "not shipped (backorder)",
        },
        "issue": {
            "item": item["name"],
            "brief": sc.issue_brief,
            "customer_asks": sc.customer_asks,
            "days_since_order": sc.facts["order"]["days_since_order"],
        },
        "staff_actions": actions,
        "outcome": final,
        "turns": turns,
    }
