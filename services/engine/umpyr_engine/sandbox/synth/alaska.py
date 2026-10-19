"""Alaska Airlines synthetic bookings and tickets (~300): cancellations/refunds vs credit, delays (controllable vs not),
bag service guarantee, delayed/damaged bags, seat/ancillary refunds, loyalty (Atmos Rewards) questions."""

from __future__ import annotations

import random
import string
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from umpyr_rules import RulePack, evaluate

from .core import NOW, Person, Scenario, StaffStep, iso, make_person, money

AIRPORTS = [
    "SEA",
    "PDX",
    "ANC",
    "LAX",
    "SFO",
    "SJC",
    "SAN",
    "LAS",
    "PHX",
    "DEN",
    "ORD",
    "JFK",
    "BOS",
    "HNL",
    "OGG",
    "FAI",
    "JNU",
    "BOI",
    "GEG",
    "SLC",
]
FARES = [("saver", 0.30), ("main", 0.50), ("premium", 0.10), ("first", 0.10)]
TIERS = ["none", "none", "none", "Member", "Member", "MVP", "MVP Gold", "MVP Gold 75K"]
CAT_HOURS = {
    "flight_cancellation_refund": (2, 24),
    "flight_delay_compensation": (4, 48),
    "customer_cancellation_refund_vs_credit": (1, 12),
    "schedule_change": (2, 24),
    "bag_service_guarantee": (1, 6),
    "delayed_bag": (12, 96),
    "damaged_bag": (12, 72),
    "seat_ancillary_refund": (2, 24),
    "loyalty_points": (4, 48),
}
MIX = [
    ("flight_cancellation_refund", 45),
    ("flight_delay_compensation", 45),
    ("customer_cancellation_refund_vs_credit", 55),
    ("schedule_change", 25),
    ("bag_service_guarantee", 30),
    ("delayed_bag", 35),
    ("damaged_bag", 25),
    ("seat_ancillary_refund", 20),
    ("loyalty_points", 20),
]


def _pnr(rng: random.Random) -> str:
    return "".join(rng.choice(string.ascii_uppercase) for _ in range(6))


def make_booking(
    rng: random.Random,
    idx: int,
    person: Person,
    purchased_at: datetime,
    depart_at: datetime,
    *,
    bags: int = 1,
    seat_fee: float = 0.0,
) -> dict:
    fare = rng.choices([f for f, _ in FARES], weights=[w for _, w in FARES], k=1)[0]
    origin, dest = rng.sample(AIRPORTS, 2)
    base = {
        "saver": rng.randint(79, 260),
        "main": rng.randint(139, 420),
        "premium": rng.randint(220, 560),
        "first": rng.randint(380, 900),
    }[fare]
    total = base + rng.randint(11, 40)
    bag_fee = 35.0
    bag_list = [
        {
            "bag_tag": f"AS{rng.randint(100000, 999999)}",
            "status": "delivered",
            "checked": True,
            "fee_paid": money(bag_fee if i == 0 else 45.0),
            "damage_type": "none",
        }
        for i in range(bags)
    ]
    tier = rng.choice(TIERS)
    flights = [
        {
            "flight_id": f"F{idx}-1",
            "flight_number": f"AS{rng.randint(100, 1999)}",
            "origin": origin,
            "destination": dest,
            "scheduled_departure": iso(depart_at),
            "status": "scheduled" if depart_at > NOW else "completed",
            "delay_minutes": 0,
            "arrival_delay_minutes": 0,
            "cause": "unknown",
            "is_cancelled_by_airline": False,
            "schedule_change_minutes": 0,
            "is_significant_change": False,
            "customer_accepted_alternative": False,
            "overnight_required": False,
            "is_domestic": True,
        }
    ]
    return {
        "pnr": _pnr(rng),
        "order_id": None,  # set below for a uniform key
        "ticket_number": f"027{rng.randint(1000000000, 9999999999)}",
        "fare_class": fare,
        "is_refundable_fare": fare == "first" and rng.random() < 0.5,
        "purchased_at": iso(purchased_at),
        "days_until_departure_at_purchase": max(0, (depart_at - purchased_at).days),
        "total_paid": money(total),
        "currency": "USD",
        "payment_method": rng.choices(["card", "card", "card", "travel_credit", "points"], k=1)[0],
        "paid_with_credit_amount": money(0),
        "ancillary_seat_fee": money(seat_fee),
        "ancillary_bag_fee": money(sum(float(b["fee_paid"]) for b in bag_list)),
        "ancillary_other_fee": money(0),
        "status": "active",
        "customer_cancelled": False,
        "travel_completed": depart_at < NOW,
        "loyalty_number": f"{rng.randint(10000000, 99999999)}" if tier != "none" else None,
        "flights": flights,
        "bags": bag_list,
        "customer": {
            "customer_id": person.customer_id,
            "name": person.name,
            "email": person.email,
            "phone": person.phone,
            "address": person.address,
            "loyalty_member": tier != "none",
            "loyalty_tier": tier,
        },
    }


def build_facts(booking: dict, claim: dict, at: datetime) -> dict[str, Any]:
    purchased = datetime.fromisoformat(booking["purchased_at"].rstrip("Z"))
    flight = booking["flights"][0]
    dep = datetime.fromisoformat(flight["scheduled_departure"].rstrip("Z"))
    b = {k: v for k, v in booking.items() if k not in ("flights", "bags", "customer", "order_id") and v is not None}
    b["hours_since_purchase"] = int((at - purchased).total_seconds() // 3600)
    b["hours_until_departure"] = int((dep - at).total_seconds() // 3600)
    facts: dict[str, Any] = {
        "customer": {
            "customer_id": booking["customer"]["customer_id"],
            "loyalty_member": booking["customer"]["loyalty_member"],
            "loyalty_tier": booking["customer"]["loyalty_tier"],
        },
        "booking": b,
        "flight": {k: v for k, v in flight.items() if v is not None},
        "claim": {k: v for k, v in claim.items() if v is not None},
    }
    if booking["bags"]:
        facts["bag"] = {k: v for k, v in booking["bags"][0].items() if v is not None}
    return facts


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


def generate(pack: RulePack, n: int = 300, seed: int = 11) -> tuple[list[dict], list[Scenario], list[str]]:
    rng = random.Random(seed)
    bookings: list[dict] = []
    scenarios: list[Scenario] = []
    names: list[str] = []
    cats = [c for c, w in MIX for _ in range(w)]
    rng.shuffle(cats)
    cats = cats[:n]
    for i, cat in enumerate(cats):
        person = make_person(rng, 5000 + i)
        names.append(person.name)
        sc, booking = _scenario(rng, cat, person, pack, i)
        booking["order_id"] = booking["pnr"]
        bookings.append(booking)
        scenarios.append(sc)
    return bookings, scenarios, names


def _scenario(rng: random.Random, cat: str, person: Person, pack: RulePack, i: int) -> tuple[Scenario, dict]:
    ticket_id = f"AK-{30000 + i}"
    claim: dict[str, Any] = {}
    steps: list[StaffStep] = [StaffStep("verify_booking", "booking and fare rules checked")]
    brief = asks = ""
    purchased = NOW - timedelta(days=rng.randint(10, 90))
    depart = NOW - timedelta(days=rng.randint(1, 20))  # most scenarios are post-travel
    bags = rng.choice([0, 1, 1, 2])
    seat_fee = rng.choice([0, 0, 15, 29, 49])
    booking = make_booking(rng, i, person, purchased, depart, bags=bags, seat_fee=seat_fee)
    flight = booking["flights"][0]
    at = depart + timedelta(hours=rng.randint(2, 72))

    if cat == "flight_cancellation_refund":
        accepted = rng.random() < 0.35
        cause = rng.choice(["controllable", "uncontrollable", "controllable"])
        flight.update(
            {
                "status": "cancelled",
                "is_cancelled_by_airline": True,
                "cause": cause,
                "cause_detail": "crew" if cause == "controllable" else "weather",
                "customer_accepted_alternative": accepted,
                "overnight_required": rng.random() < 0.3,
            }
        )
        claim = {
            "reason": "flight_cancelled",
            "requested_remedy": "refund" if not accepted else "expense_reimbursement",
        }
        brief = (
            f"Flight {flight['flight_number']} {flight['origin']}-{flight['destination']} was cancelled by the airline ({flight['cause_detail']})."
            + (
                " Customer took the rebooked flight the next day."
                if accepted
                else " Customer declined the rebooking and did not travel."
            )
        )
        asks = "wants a full refund to the original card" if not accepted else "wants hotel/meal costs reimbursed"
        steps.append(StaffStep("check_flight_status", "cancellation and cause confirmed"))
    elif cat == "flight_delay_compensation":
        mins = rng.choice([45, 95, 140, 200, 260, 400])
        cause = rng.choice(["controllable", "uncontrollable"])
        overnight = mins >= 260 and rng.random() < 0.5
        flight.update(
            {
                "status": "delayed",
                "delay_minutes": mins,
                "arrival_delay_minutes": mins - rng.randint(0, 20),
                "cause": cause,
                "cause_detail": rng.choice(["maintenance", "crew"])
                if cause == "controllable"
                else rng.choice(["weather", "atc"]),
                "overnight_required": overnight,
            }
        )
        flew = rng.random() < 0.7
        flight["customer_accepted_alternative"] = flew
        claim = {
            "reason": "flight_delayed",
            "requested_remedy": "expense_reimbursement" if flew else "refund",
            "requested_amount": money(rng.choice([35, 60, 120, 210])) if flew else None,
        }
        brief = f"Flight {flight['flight_number']} departed {mins} minutes late ({flight['cause_detail']})." + (
            " Customer flew and paid for meals/hotel out of pocket." if flew else " Customer chose not to travel."
        )
        asks = "wants meal/hotel expenses reimbursed" if flew else "wants a refund because they did not travel"
        steps.append(StaffStep("check_flight_status", "delay length and cause confirmed"))
        steps.append(StaffStep("confirm_cause", flight["cause"]))
    elif cat == "customer_cancellation_refund_vs_credit":
        depart = NOW + timedelta(days=rng.choice([2, 5, 10, 16, 30, 45]))
        flight["scheduled_departure"] = iso(depart)
        flight["status"] = "scheduled"
        booking["travel_completed"] = False
        hours_since = rng.choice([5, 18, 30, 72, 240, 800])
        purchased = NOW - timedelta(hours=hours_since)
        booking["purchased_at"] = iso(purchased)
        booking["days_until_departure_at_purchase"] = (depart - purchased).days
        at = NOW
        booking["customer_cancelled"] = True
        claim = {"reason": "refund_request", "requested_remedy": "refund", "wants_credit_instead_of_refund": False}
        brief = f"Customer needs to cancel a {booking['fare_class']} fare {flight['origin']}-{flight['destination']} departing in {(depart - NOW).days} days, booked {hours_since} hours ago."
        asks = "wants a refund to the original payment, or at least a credit"
        steps.append(StaffStep("check_fare_rules", booking["fare_class"]))
    elif cat == "schedule_change":
        depart = NOW + timedelta(days=rng.randint(10, 60))
        flight["scheduled_departure"] = iso(depart)
        flight["status"] = "scheduled"
        booking["travel_completed"] = False
        mins = rng.choice([35, 70, 125, 190, 300])
        flight.update(
            {
                "schedule_change_minutes": mins,
                "is_significant_change": mins > 120,
                "customer_accepted_alternative": False,
            }
        )
        at = NOW
        claim = {"reason": "schedule_change", "requested_remedy": "refund"}
        brief = f"Alaska moved flight {flight['flight_number']} by {mins} minutes after purchase; the new time does not work for the customer."
        asks = "wants a full refund rather than the new time"
        steps.append(StaffStep("check_flight_status", f"schedule change {mins} min"))
    elif cat == "bag_service_guarantee":
        mins_to_carousel = rng.choice([17, 24, 31, 45, 60])
        claimed_within = rng.choice([1, 1, 2, 5, 30])
        booking["bags"] = booking["bags"] or [
            {
                "bag_tag": f"AS{rng.randint(100000, 999999)}",
                "status": "delivered",
                "checked": True,
                "fee_paid": money(35),
                "damage_type": "none",
            }
        ]
        booking["bags"][0].update(
            {"minutes_to_carousel": mins_to_carousel, "guarantee_claim_filed_within_hours": claimed_within}
        )
        at = depart + timedelta(hours=rng.randint(6, 48))
        claim = {"reason": "bag_guarantee", "requested_remedy": rng.choice(["voucher", "points"])}
        brief = f"Checked bag took about {mins_to_carousel} minutes to reach the carousel." + (
            " Customer spoke to the baggage office within two hours."
            if claimed_within <= 2
            else f" Customer is writing in {claimed_within} hours after arrival without having claimed at the airport."
        )
        asks = "wants the 20-minute bag guarantee ($25 code or 2,500 points)"
        steps.append(StaffStep("check_flight_status", "arrival and bag scan times checked"))
    elif cat == "delayed_bag":
        delay_h = rng.choice([8, 14, 26, 48, 120])
        report_filed = rng.random() < 0.8
        booking["bags"] = booking["bags"] or [
            {
                "bag_tag": f"AS{rng.randint(100000, 999999)}",
                "status": "delayed",
                "checked": True,
                "fee_paid": money(35),
                "damage_type": "none",
            }
        ]
        booking["bags"][0].update(
            {
                "status": "lost" if delay_h >= 120 else "delayed",
                "report_filed": report_filed,
                "report_filed_within_hours": rng.choice([1, 3, 20]) if report_filed else None,
                "delay_days": delay_h // 24,
                "interim_expenses": money(rng.choice([0, 40, 85, 160])),
            }
        )
        at = depart + timedelta(hours=delay_h + rng.randint(2, 30))
        claim = {
            "reason": "bag_lost" if delay_h >= 120 else "bag_delayed",
            "requested_remedy": "refund" if rng.random() < 0.5 else "expense_reimbursement",
        }
        brief = (
            f"Checked bag was delayed about {delay_h} hours."
            + (" A mishandled baggage report is on file." if report_filed else " No report was filed at the airport.")
            + f" Customer bought essentials (${booking['bags'][0]['interim_expenses']})."
        )
        asks = "wants the bag fee refunded and expenses covered"
        steps.append(
            StaffStep("request_report" if not report_filed else "check_flight_status", "report status checked")
        )
    elif cat == "damaged_bag":
        within = rng.choice([0, 1, 12, 30, 72])
        dtype = rng.choice(["structural", "wheel", "handle", "zipper", "cosmetic"])
        booking["bags"] = booking["bags"] or [
            {"bag_tag": f"AS{rng.randint(100000, 999999)}", "status": "damaged", "checked": True, "fee_paid": money(35)}
        ]
        booking["bags"][0].update(
            {
                "status": "damaged",
                "damage_type": dtype,
                "report_filed": within <= 24,
                "report_filed_within_hours": within if within <= 24 else None,
            }
        )
        at = depart + timedelta(hours=within + rng.randint(2, 24))
        claim = {
            "reason": "bag_damaged",
            "requested_remedy": "expense_reimbursement",
            "requested_amount": money(rng.choice([60, 120, 250, 400])),
            "has_photos": rng.random() < 0.7,
        }
        brief = f"Checked bag arrived with {dtype} damage." + (
            f" Reported to an agent {within} hours after arrival."
            if within <= 24
            else f" Customer only noticed/reported {within} hours later."
        )
        asks = "wants repair or replacement cost covered"
        steps.append(StaffStep("request_report" if within > 24 else "review_photos", "damage report status"))
    elif cat == "seat_ancillary_refund":
        booking["ancillary_seat_fee"] = money(rng.choice([15, 29, 49, 89]))
        not_provided = rng.random() < 0.7
        claim = {
            "reason": "seat_refund",
            "requested_remedy": "refund",
            "requested_amount": booking["ancillary_seat_fee"],
        }
        brief = (
            f"Paid ${booking['ancillary_seat_fee']} for a seat selection but was moved to a different seat at the gate."
            if not_provided
            else f"Paid ${booking['ancillary_seat_fee']} for a seat upgrade, sat in it, and now wants the fee back because the flight was delayed."
        )
        booking["flights"][0]["cause_detail"] = "seat_not_provided" if not_provided else "seat_provided"
        asks = "wants the seat fee refunded"
        steps.append(StaffStep("verify_booking", "ancillary purchase verified"))
    else:  # loyalty_points
        claim = {"reason": "loyalty_points", "requested_remedy": "points"}
        brief = rng.choice(
            [
                "Atmos Rewards points for the flight never posted to the account.",
                "Customer asks how many points the flight should earn and when they post.",
                "Customer wants points from a partner flight credited.",
            ]
        )
        asks = "wants the missing points credited"
        steps.append(StaffStep("verify_booking", "loyalty number on booking checked"))

    booking["flights"][0] = flight
    facts = build_facts(booking, claim, at)
    ruling = evaluate(pack, facts)
    policy = _ruling_summary(ruling)
    staff_outcome, more, drift, reason = _staff_outcome(rng, cat, ruling, booking, claim)
    steps.extend(more)
    lo, hi = CAT_HOURS[cat]
    hours = round(rng.uniform(lo, hi) * (1.5 if drift else 1.0), 1)
    sc = Scenario(
        ticket_id=ticket_id,
        category_key=cat,
        claim_reason=claim["reason"],
        person=person,
        order_id=booking["pnr"],
        created_at=at,
        facts=facts,
        issue_brief=brief,
        customer_asks=asks,
        policy=policy,
        staff_outcome=staff_outcome,
        staff_steps=steps,
        hours_to_resolve=hours,
        is_drift=drift,
        drift_reason=reason,
        channel=rng.choice(["email", "email", "web_form", "chat"]),
    )
    return sc, booking


def _staff_outcome(
    rng: random.Random, cat: str, ruling, booking: dict, claim: dict
) -> tuple[dict, list[StaffStep], bool, str | None]:
    total = Decimal(str(booking["total_paid"]))
    decision, remedy, amount, fault = str(ruling.decision), ruling.remedy, ruling.amount, ruling.fault
    steps: list[StaffStep] = []
    drift = rng.random() < 0.15

    def out(d, r, a, f, note=""):
        return {"decision": d, "remedy": r, "amount": str(a) if a is not None else None, "fault": f, "note": note}

    action_for = {
        "refund": "issue_refund",
        "travel_credit": "issue_travel_credit",
        "voucher": "issue_bag_guarantee",
        "points": "award_points",
        "expense_reimbursement": "reimburse_expenses",
        "rebook": "rebook",
        "partial_refund": "issue_partial_refund",
    }
    if decision in ("approve", "partial") and remedy not in (None, "none"):
        if drift and remedy == "refund":
            steps.append(StaffStep("issue_travel_credit", "credit issued instead of refund", amount or total))
            return (
                out("approve", "travel_credit", amount or total, fault),
                steps,
                True,
                f"Travel credit issued where policy requires a refund to original payment ({ruling.winning_rule_id}).",
            )
        if drift and remedy in ("voucher", "points", "expense_reimbursement"):
            steps.append(StaffStep("issue_refund", "goodwill refund of the fare", total))
            return (
                out("approve", "refund", total, "airline"),
                steps,
                True,
                f"Full fare refunded where policy provides {remedy} ({ruling.winning_rule_id}).",
            )
        steps.append(StaffStep(action_for.get(remedy, "other"), f"{remedy} issued", amount))
        return out(decision, remedy, amount, fault), steps, False, None
    if decision == "approve":
        return out("approve", "none", None, fault, "informational reply"), steps, False, None
    if decision == "deny":
        if drift:
            goodwill = rng.choice(["travel_credit", "voucher", "points"])
            steps.append(
                StaffStep(
                    action_for[goodwill], "goodwill exception", money(50) if goodwill != "points" else Decimal(2500)
                )
            )
            return (
                out("approve", goodwill, money(50) if goodwill != "points" else Decimal(2500), "airline"),
                steps,
                True,
                f"Goodwill {goodwill} granted although policy denies ({ruling.winning_rule_id}).",
            )
        steps.append(StaffStep("check_policy", "policy checked"))
        steps.append(StaffStep("deny_request", ruling.explanation[:120]))
        return out("deny", "none", None, fault), steps, False, None
    if decision == "needs_approval":
        steps.append(StaffStep("escalate_supervisor", "sent for authorization"))
        steps.append(StaffStep(action_for.get(remedy or "refund", "issue_refund"), "approved by supervisor", amount))
        return out("approve", remedy or "refund", amount, fault), steps, False, None
    if decision == "needs_fact":
        steps.append(StaffStep("request_report", "asked for the missing information"))
        steps.append(StaffStep("issue_refund", "resolved after information received", amount))
        return out("approve", remedy or "refund", amount, fault), steps, False, None
    # escalate: human judgement
    steps.append(StaffStep("escalate_supervisor", "no governing clause; supervisor decided"))
    if cat == "loyalty_points":
        steps.append(StaffStep("award_points", "points posted manually", Decimal(rng.choice([550, 1200, 2400]))))
        return out("approve", "points", Decimal(1200), None, "human decision"), steps, False, None
    steps.append(StaffStep("deny_request", "declined after review"))
    return out("deny", "none", None, None, "human decision"), steps, False, None


AGENTS = ["Jordan", "Sam", "Taylor", "Casey", "Morgan", "Riley", "Avery", "Quinn"]


def turn_plan(rng: random.Random, sc: Scenario, booking: dict, _item: dict) -> dict[str, Any]:
    actions = [s.action for s in sc.staff_steps]
    turns: list[dict[str, str]] = [
        {
            "role": "customer",
            "purpose": f"Describe the issue: {sc.issue_brief} Mention confirmation code {booking['pnr']}. {sc.customer_asks}.",
        }
    ]
    if "request_report" in actions or "request_photos" in actions:
        turns.append(
            {
                "role": "staff",
                "purpose": "Apologise, confirm the booking, and ask for the missing information (bag report file ID / photos / receipts).",
            }
        )
        turns.append({"role": "customer", "purpose": "Provide the requested information."})
    final = sc.staff_outcome
    outcome_text = {
        (
            "approve",
            "refund",
        ): f"Confirm a refund of ${final['amount']} to the original form of payment within 7 business days.",
        (
            "approve",
            "travel_credit",
        ): f"Confirm a travel credit of ${final['amount']} valid one year, and explain how to use it.",
        ("approve", "voucher"): "Confirm the $25 discount code (bag service guarantee) is being emailed.",
        ("approve", "points"): f"Confirm {final['amount']} Atmos Rewards points will post to the account.",
        (
            "approve",
            "expense_reimbursement",
        ): f"Confirm reimbursement of ${final['amount']} for reasonable expenses with receipts.",
        ("partial", "travel_credit"): f"Explain the fare rule and confirm a credit of ${final['amount']}.",
        (
            "deny",
            "none",
        ): "Politely decline, state the policy reason in plain words, and mention any alternative that exists.",
        ("approve", "none"): "Answer the question clearly and close.",
    }.get(
        (final["decision"], final["remedy"]),
        f"Communicate the outcome: {final['decision']} / {final['remedy']} ({final.get('note', '')}).",
    )
    if "escalate_supervisor" in actions:
        turns.append(
            {"role": "staff", "purpose": "Acknowledge and say the case has been sent for review; set expectations."}
        )
    turns.append({"role": "staff", "purpose": outcome_text})
    if rng.random() < 0.5:
        turns.append(
            {
                "role": "customer",
                "purpose": rng.choice(
                    ["Thank them briefly.", "Ask a short follow-up (timing).", "Push back once, then accept."]
                ),
            }
        )
        if turns[-1]["purpose"] != "Thank them briefly.":
            turns.append({"role": "staff", "purpose": "Answer briefly and close warmly."})
    f = booking["flights"][0]
    return {
        "merchant": "Alaska Airlines guest care (airline)",
        "agent_name": rng.choice(AGENTS),
        "customer": {
            "name": sc.person.name,
            "email": sc.person.email,
            "phone": sc.person.phone,
            "address": sc.person.address,
        },
        "booking": {
            "confirmation_code": booking["pnr"],
            "fare": booking["fare_class"],
            "route": f"{f['origin']}-{f['destination']}",
            "flight": f["flight_number"],
            "departure": f["scheduled_departure"][:16],
            "total_paid": str(booking["total_paid"]),
            "loyalty_tier": booking["customer"]["loyalty_tier"],
            "bags_checked": len(booking["bags"]),
            "seat_fee": str(booking["ancillary_seat_fee"]),
        },
        "issue": {"brief": sc.issue_brief, "customer_asks": sc.customer_asks},
        "staff_actions": actions,
        "outcome": final,
        "turns": turns,
    }
