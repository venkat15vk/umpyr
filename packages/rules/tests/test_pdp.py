from decimal import Decimal

import pytest
from umpyr_rules import Actor, PDPPolicy, Resource, Ruling, authorize
from umpyr_rules.pdp import Action, Mode, PDPDecision

AGENT = Actor(actor_id="umpyr-agent", role="agent")
CUSTOMER_AGENT = Actor(actor_id="muse", role="customer_agent")


def ruling(decision="approve", remedy="refund", amount="80.00", clause="RK-RET-1"):
    return Ruling(
        decision=decision,
        remedy=remedy,
        amount=Decimal(amount) if amount is not None else None,
        winning_rule_id=f"{clause}.r1",
        winning_clause_id=clause,
        clause_ids=[clause],
        rule_pack_version="v1",
    )


def policy(**kw):
    return PDPPolicy(workspace="rockler", **kw)


def res(amount="80.00", max_amount="129.99"):
    return Resource(type="order", id="R-1", amount=Decimal(amount), max_amount=Decimal(max_amount))


def test_allow_within_limit():
    a = authorize(AGENT, "refund", res(), {}, ruling(), policy(auto_approve_limit=Decimal("100")))
    assert a.decision == PDPDecision.allow
    assert a.clause_ids == ["RK-RET-1"]
    assert any("licensed by rule RK-RET-1.r1" in r for r in a.reasons)


def test_needs_approval_above_limit():
    a = authorize(AGENT, "refund", res(), {}, ruling(), policy(auto_approve_limit=Decimal("50")))
    assert a.decision == PDPDecision.needs_approval
    assert a.limit == Decimal("50") and a.amount == Decimal("80.00")


def test_limit_is_inclusive():
    a = authorize(
        AGENT, "refund", res("100.00"), {}, ruling(amount="100.00"), policy(auto_approve_limit=Decimal("100"))
    )
    assert a.decision == PDPDecision.allow


def test_deny_when_ruling_denies():
    a = authorize(AGENT, "refund", res(), {}, ruling(decision="deny", remedy="none", amount=None), policy())
    assert a.decision == PDPDecision.deny


@pytest.mark.parametrize("decision", ["needs_fact", "escalate"])
def test_deny_when_ruling_unresolved(decision):
    a = authorize(AGENT, "refund", res(), {}, ruling(decision=decision), policy())
    assert a.decision == PDPDecision.deny
    assert "resolve before acting" in a.reasons[-1]


def test_deny_unlicensed_action():
    a = authorize(AGENT, "store_credit", res(), {}, ruling(remedy="refund"), policy())
    assert a.decision == PDPDecision.deny
    assert "not licensed by remedy" in a.reasons[-1]


def test_deny_amount_above_ruling_or_resource():
    a = authorize(AGENT, "refund", res("90.00"), {}, ruling(amount="80.00"), policy(auto_approve_limit=Decimal("1000")))
    assert a.decision == PDPDecision.deny and "exceeds the ruled amount" in a.reasons[-1]
    a = authorize(
        AGENT,
        "refund",
        res("150.00", "129.99"),
        {},
        ruling(amount="150.00"),
        policy(auto_approve_limit=Decimal("1000")),
    )
    assert a.decision == PDPDecision.deny and "resource maximum" in a.reasons[-1]


def test_deny_without_ruling_or_amount():
    assert authorize(AGENT, "refund", res(), {}, None, policy()).decision == PDPDecision.deny
    r = Resource(type="order", id="R-1")
    assert authorize(AGENT, "refund", r, {}, ruling(), policy()).decision == PDPDecision.deny


def test_assist_and_shadow_modes():
    a = authorize(AGENT, "refund", res(), {}, ruling(), policy(mode=Mode.assist))
    assert a.decision == PDPDecision.needs_approval and "assist mode" in a.reasons[-1]
    a = authorize(AGENT, "refund", res(), {}, ruling(), policy(mode=Mode.shadow))
    assert a.decision == PDPDecision.needs_approval and "shadow mode" in a.reasons[-1]
    # replies are still allowed in shadow mode, structural actions are recorded only
    assert authorize(AGENT, "reply", res(), {}, None, policy(mode=Mode.shadow)).decision == PDPDecision.allow
    assert authorize(AGENT, "close", res(), {}, None, policy(mode=Mode.shadow)).decision == PDPDecision.needs_approval


def test_ruling_needs_approval_propagates():
    a = authorize(AGENT, "refund", res("10"), {}, ruling(decision="needs_approval", amount="10"), policy())
    assert a.decision == PDPDecision.needs_approval


def test_role_restrictions():
    a = authorize(CUSTOMER_AGENT, "refund", res(), {}, ruling(), policy())
    assert a.decision == PDPDecision.deny and "role 'customer_agent'" in a.reasons[-1]
    assert authorize(CUSTOMER_AGENT, "reply", res(), {}, None, policy()).decision == PDPDecision.allow


def test_non_money_action_ignores_limit():
    r = Resource(type="order", id="R-1")
    a = authorize(
        AGENT,
        Action.replacement,
        r,
        {},
        ruling(remedy="replacement", amount=None),
        policy(auto_approve_limit=Decimal(0)),
    )
    assert a.decision == PDPDecision.allow


def test_authorization_is_deterministic():
    outs = {authorize(AGENT, "refund", res(), {}, ruling(), policy()).model_dump_json() for _ in range(20)}
    assert len(outs) == 1
