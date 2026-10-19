from decimal import Decimal

import pytest
from umpyr_rules import Clause, Effect, Rule, RulePack


def clause(cid, tier, text="source text"):
    return Clause(
        clause_id=cid,
        title=cid,
        text=text,
        source_url="https://example.com/policy",
        tier=tier,
        fetched_at="2026-10-19",
    )


@pytest.fixture
def pack() -> RulePack:
    return RulePack(
        workspace="rockler",
        version="test.1",
        clauses=[
            clause("RK-RET-1", "contract", "Return within 90 days for a refund."),
            clause("RK-RET-2", "contract", "Clearance items are final sale."),
            clause("RK-RET-3", "contract", "Opened power tools incur a 20% restocking fee."),
            clause("RK-DMG-1", "commitment", "Damaged on arrival: report within 7 days with photos for a replacement."),
            clause("RK-GW-1", "goodwill", "Loyalty members may get a courtesy credit."),
            clause("LAW-1", "law", "Automatic refund when the carrier cancels and the customer declines alternatives."),
        ],
        rules=[
            Rule(
                rule_id="RK-RET-1.r1",
                clause_id="RK-RET-1",
                tier="contract",
                priority=0,
                claim_reasons=["change_of_mind"],
                requires=["shipment.days_since_delivery", "item.line_total"],
                when={"<=": [{"var": "shipment.days_since_delivery"}, 90]},
                effect=Effect(decision="approve", remedy="refund", amount={"var": "item.line_total"}, fault="customer"),
                description="Standard 90-day return",
            ),
            Rule(
                rule_id="RK-RET-1.r2",
                clause_id="RK-RET-1",
                tier="contract",
                priority=0,
                claim_reasons=["change_of_mind"],
                requires=["shipment.days_since_delivery"],
                when={">": [{"var": "shipment.days_since_delivery"}, 90]},
                effect=Effect(decision="deny", remedy="none", fault="customer", note="Outside 90-day window"),
            ),
            Rule(
                rule_id="RK-RET-2.r1",
                clause_id="RK-RET-2",
                tier="contract",
                priority=10,
                claim_reasons=["change_of_mind"],
                requires=["item.is_final_sale"],
                when={"==": [{"var": "item.is_final_sale"}, True]},
                effect=Effect(decision="deny", remedy="none", fault="customer", note="Final sale"),
            ),
            Rule(
                rule_id="RK-RET-3.r1",
                clause_id="RK-RET-3",
                tier="contract",
                priority=5,
                claim_reasons=["change_of_mind"],
                requires=["item.is_power_tool", "claim.item_unopened", "item.line_total"],
                when={
                    "and": [
                        {"==": [{"var": "item.is_power_tool"}, True]},
                        {"==": [{"var": "claim.item_unopened"}, False]},
                        {"<=": [{"var": "shipment.days_since_delivery"}, 90]},
                    ]
                },
                effect=Effect(
                    decision="partial",
                    remedy="partial_refund",
                    amount={"*": [{"var": "item.line_total"}, 0.8]},
                    fault="customer",
                ),
            ),
            Rule(
                rule_id="RK-DMG-1.r1",
                clause_id="RK-DMG-1",
                tier="commitment",
                priority=0,
                claim_reasons=["damaged_on_arrival"],
                requires=["claim.has_photos", "claim.reported_within_days"],
                when={
                    "and": [
                        {"==": [{"var": "claim.has_photos"}, True]},
                        {"<=": [{"var": "claim.reported_within_days"}, 7]},
                    ]
                },
                effect=Effect(decision="approve", remedy="replacement", fault="carrier"),
            ),
            Rule(
                rule_id="RK-GW-1.r1",
                clause_id="RK-GW-1",
                tier="goodwill",
                priority=0,
                claim_reasons=["change_of_mind"],
                requires=["customer.loyalty_member"],
                when={"==": [{"var": "customer.loyalty_member"}, True]},
                effect=Effect(decision="approve", remedy="store_credit", amount=25),
            ),
            Rule(
                rule_id="LAW-1.r1",
                clause_id="LAW-1",
                tier="law",
                priority=0,
                claim_reasons=["flight_cancelled"],
                requires=[
                    "flight.is_cancelled_by_airline",
                    "flight.customer_accepted_alternative",
                    "booking.total_paid",
                ],
                when={
                    "and": [
                        {"==": [{"var": "flight.is_cancelled_by_airline"}, True]},
                        {"==": [{"var": "flight.customer_accepted_alternative"}, False]},
                    ]
                },
                effect=Effect(
                    decision="approve", remedy="refund", amount={"var": "booking.total_paid"}, fault="airline"
                ),
            ),
        ],
    )


@pytest.fixture
def return_facts():
    return {
        "claim": {"reason": "change_of_mind", "item_unopened": True},
        "item": {"line_total": Decimal("129.99"), "is_final_sale": False, "is_power_tool": False},
        "shipment": {"days_since_delivery": 30},
        "customer": {"loyalty_member": False},
    }
