import copy
from decimal import Decimal

import pytest
from umpyr_rules import Effect, Rule, RulePack, audit_hash, evaluate
from umpyr_rules.model import Clause, validate_rule


def test_standard_return_approves_full_refund(pack, return_facts):
    r = evaluate(pack, return_facts)
    assert r.decision == "approve"
    assert r.remedy == "refund"
    assert r.amount == Decimal("129.99")
    assert r.winning_rule_id == "RK-RET-1.r1"
    assert r.winning_clause_id == "RK-RET-1"
    assert r.tier == "contract"
    assert r.fault == "customer"
    # goodwill rule also fires but is outranked; it is still listed
    assert "RK-GW-1.r1" not in r.fired_rule_ids  # loyalty_member False -> not fired
    checks = {c.rule_id: c for c in r.checks}
    assert checks["RK-RET-1.r1"].fired and checks["RK-RET-1.r1"].fields["shipment.days_since_delivery"] == 30
    assert checks["LAW-1.r1"].applicable is False


def test_same_facts_same_ruling_same_hash(pack, return_facts):
    a = evaluate(pack, return_facts)
    b = evaluate(pack, copy.deepcopy(return_facts))
    assert a == b
    assert audit_hash(a) == audit_hash(b)


def test_priority_exception_beats_default(pack, return_facts):
    return_facts["item"]["is_final_sale"] = True
    r = evaluate(pack, return_facts)
    assert r.decision == "deny"
    assert r.winning_rule_id == "RK-RET-2.r1"
    assert "RK-RET-1.r1" in r.fired_rule_ids
    assert r.clause_ids[0] == "RK-RET-2"


def test_partial_with_decimal_rounding(pack, return_facts):
    return_facts["item"].update({"is_power_tool": True, "line_total": Decimal("129.99")})
    return_facts["claim"]["item_unopened"] = False
    r = evaluate(pack, return_facts)
    assert r.decision == "partial"
    assert r.remedy == "partial_refund"
    assert r.amount == Decimal("103.99")  # 103.992 -> 103.99
    return_facts["item"]["line_total"] = Decimal("10.03")  # 8.024 -> 8.02 ; check half-up with 10.06 -> 8.048 -> 8.05
    assert evaluate(pack, return_facts).amount == Decimal("8.02")
    return_facts["item"]["line_total"] = Decimal("10.06")
    assert evaluate(pack, return_facts).amount == Decimal("8.05")


def test_law_tier_beats_contract(pack):
    facts = {
        "claim": {"reason": "flight_cancelled"},
        "flight": {"is_cancelled_by_airline": True, "customer_accepted_alternative": False},
        "booking": {"total_paid": Decimal("412.40")},
    }
    r = evaluate(pack, facts)
    assert r.decision == "approve" and r.tier == "law" and r.amount == Decimal("412.40")


def test_needs_fact_lists_missing_sorted(pack):
    facts = {"claim": {"reason": "damaged_on_arrival"}}
    r = evaluate(pack, facts)
    assert r.decision == "needs_fact"
    assert r.missing_facts == ["claim.has_photos", "claim.reported_within_days"]
    facts["claim"]["has_photos"] = True
    r = evaluate(pack, facts)
    assert r.missing_facts == ["claim.reported_within_days"]
    facts["claim"]["reported_within_days"] = 3
    r = evaluate(pack, facts)
    assert r.decision == "approve" and r.remedy == "replacement" and r.fault == "carrier"


def test_needs_fact_when_amount_var_missing(pack, return_facts):
    del return_facts["item"]["line_total"]
    r = evaluate(pack, return_facts)
    # line_total is in requires so it is reported as missing before evaluation
    assert r.decision == "needs_fact" and "item.line_total" in r.missing_facts


def test_amount_missing_after_fire_yields_needs_fact():
    pack = RulePack(
        workspace="w",
        version="v",
        clauses=[Clause(clause_id="C1", title="c", text="t", source_url="u", tier="contract")],
        rules=[
            Rule(
                rule_id="C1.r1",
                clause_id="C1",
                tier="contract",
                when=True,
                effect=Effect(decision="approve", remedy="refund", amount={"var": "order.total"}),
            )
        ],
    )
    r = evaluate(pack, {})
    assert r.decision == "needs_fact" and r.missing_facts == ["order.total"]


def test_no_match_escalates(pack):
    r = evaluate(pack, {"claim": {"reason": "other"}})
    assert r.decision == "escalate"
    assert r.fired_rule_ids == []
    assert "No compiled rule matched" in r.explanation


def test_same_level_conflict_escalates(pack, return_facts):
    conflicting = Rule(
        rule_id="RK-RET-1.r9",
        clause_id="RK-RET-1",
        tier="contract",
        priority=0,
        claim_reasons=["change_of_mind"],
        when=True,
        effect=Effect(decision="deny", remedy="none"),
    )
    pack2 = pack.model_copy(update={"rules": [*pack.rules, conflicting]})
    r = evaluate(pack2, return_facts)
    assert r.decision == "escalate"
    assert len(r.conflicts) == 1 and "RK-RET-1.r1" in r.conflicts[0] and "RK-RET-1.r9" in r.conflicts[0]


def test_category_scope(pack, return_facts):
    scoped = pack.model_copy(update={"rules": [r.model_copy(update={"categories": ["returns"]}) for r in pack.rules]})
    assert evaluate(scoped, return_facts, category="damage").decision == "escalate"
    assert evaluate(scoped, return_facts, category="returns").decision == "approve"


def test_disabled_rule_is_ignored(pack, return_facts):
    return_facts["item"]["is_final_sale"] = True
    pack2 = pack.model_copy(
        update={
            "rules": [r.model_copy(update={"enabled": False}) if r.rule_id == "RK-RET-2.r1" else r for r in pack.rules]
        }
    )
    assert evaluate(pack2, return_facts).decision == "approve"


def test_rule_rejects_unknown_ontology_path():
    with pytest.raises(ValueError, match="unknown ontology path"):
        Rule(
            rule_id="x",
            clause_id="c",
            tier="contract",
            when={"==": [{"var": "order.zendesk_priority"}, 1]},
            effect=Effect(decision="deny"),
        )
    ok = Rule(
        rule_id="x",
        clause_id="c",
        tier="contract",
        when={"some": [{"var": "order.items"}, {"==": [{"var": "is_final_sale"}, True]}]},
        effect=Effect(decision="deny"),
    )
    assert validate_rule(ok) == []


def test_rulepack_rejects_dangling_clause_and_duplicate_ids():
    c = Clause(clause_id="C1", title="c", text="t", source_url="u", tier="contract")
    r = Rule(rule_id="r", clause_id="C9", tier="contract", when=True, effect=Effect(decision="deny"))
    with pytest.raises(ValueError, match="unknown clauses"):
        RulePack(workspace="w", version="v", clauses=[c], rules=[r])
    r1 = r.model_copy(update={"clause_id": "C1"})
    with pytest.raises(ValueError, match="duplicate rule ids"):
        RulePack(workspace="w", version="v", clauses=[c], rules=[r1, r1])


def test_evaluation_error_is_captured_not_raised():
    c = Clause(clause_id="C1", title="c", text="t", source_url="u", tier="contract")
    r = Rule(rule_id="r", clause_id="C1", tier="contract", when={"/": [1, 0]}, effect=Effect(decision="deny"))
    out = evaluate(RulePack(workspace="w", version="v", clauses=[c], rules=[r]), {})
    assert out.decision == "escalate"
    assert out.checks[0].error == "division by zero"
