"""Policy Decision Point.

Every action (refund, label, credit, points...) passes through `authorize`
before it executes. The PDP never decides the *ruling*; it decides whether a
given actor may execute a given action on a given resource given the ruling.

Outcomes: allow | deny | needs_approval, each with reasons and clause IDs.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from umpyr_ontology import Decision, Remedy

from .model import Ruling


class Mode(StrEnum):
    autonomous = "autonomous"  # execute within authority
    assist = "assist"  # every action needs human approval
    shadow = "shadow"  # nothing executes; decisions are recorded only


class Action(StrEnum):
    refund = "refund"
    partial_refund = "partial_refund"
    replacement = "replacement"
    reship = "reship"
    return_label = "return_label"
    store_credit = "store_credit"
    travel_credit = "travel_credit"
    rebook = "rebook"
    points = "points"
    voucher = "voucher"
    expense_reimbursement = "expense_reimbursement"
    bag_delivery = "bag_delivery"
    cancel_order = "cancel_order"
    escalate = "escalate"
    close = "close"
    reply = "reply"


# Which remedies license which actions. `escalate`, `close` and `reply` are always structurally allowed.
REMEDY_ACTIONS: dict[Remedy, set[Action]] = {
    Remedy.refund: {Action.refund},
    Remedy.partial_refund: {Action.partial_refund, Action.refund},
    Remedy.replacement: {Action.replacement, Action.return_label},
    Remedy.reship: {Action.reship},
    Remedy.repair: {Action.return_label},
    Remedy.return_label: {Action.return_label},
    Remedy.store_credit: {Action.store_credit},
    Remedy.travel_credit: {Action.travel_credit},
    Remedy.rebook: {Action.rebook},
    Remedy.points: {Action.points},
    Remedy.voucher: {Action.voucher},
    Remedy.expense_reimbursement: {Action.expense_reimbursement},
    Remedy.bag_delivery: {Action.bag_delivery},
    Remedy.cancel_order: {Action.cancel_order, Action.refund},
    Remedy.none: set(),
}

MONEY_ACTIONS = {
    Action.refund,
    Action.partial_refund,
    Action.store_credit,
    Action.travel_credit,
    Action.voucher,
    Action.expense_reimbursement,
    Action.points,
}
ALWAYS_ALLOWED = {Action.escalate, Action.close, Action.reply}


class PDPDecision(StrEnum):
    allow = "allow"
    deny = "deny"
    needs_approval = "needs_approval"


class Actor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor_id: str
    role: str = Field(..., description="agent | supervisor | customer_agent")


class Resource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = Field(..., description="order | booking | case")
    id: str
    amount: Decimal | None = Field(None, description="Money the action would move")
    max_amount: Decimal | None = Field(None, description="Upper bound, e.g. order total")


class PDPPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace: str
    mode: Mode = Mode.autonomous
    auto_approve_limit: Decimal = Field(Decimal("100"), description="Amounts above this need approval")
    allowed_roles: dict[str, list[Action]] = Field(
        default_factory=lambda: {
            "agent": [a for a in Action],
            "supervisor": [a for a in Action],
            "customer_agent": [Action.reply, Action.escalate],
        }
    )


class Authorization(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: PDPDecision
    action: Action
    actor_id: str
    reasons: list[str] = Field(default_factory=list)
    clause_ids: list[str] = Field(default_factory=list)
    amount: Decimal | None = None
    limit: Decimal | None = None


def authorize(
    actor: Actor,
    action: Action | str,
    resource: Resource,
    facts: dict[str, Any] | None,
    ruling: Ruling | None,
    policy: PDPPolicy,
) -> Authorization:
    action = Action(action)
    reasons: list[str] = []
    clause_ids = list(ruling.clause_ids) if ruling else []
    amount = resource.amount

    def deny(reason: str) -> Authorization:
        return Authorization(
            decision=PDPDecision.deny,
            action=action,
            actor_id=actor.actor_id,
            reasons=[*reasons, reason],
            clause_ids=clause_ids,
            amount=amount,
            limit=policy.auto_approve_limit,
        )

    def needs_approval(reason: str) -> Authorization:
        return Authorization(
            decision=PDPDecision.needs_approval,
            action=action,
            actor_id=actor.actor_id,
            reasons=[*reasons, reason],
            clause_ids=clause_ids,
            amount=amount,
            limit=policy.auto_approve_limit,
        )

    # 1. Role check
    allowed = set(policy.allowed_roles.get(actor.role, []))
    if action not in allowed:
        return deny(f"role '{actor.role}' may not perform '{action}'")

    if action in ALWAYS_ALLOWED:
        if policy.mode == Mode.shadow and action != Action.reply:
            return needs_approval("shadow mode: actions are recorded, not executed")
        return Authorization(
            decision=PDPDecision.allow,
            action=action,
            actor_id=actor.actor_id,
            reasons=["structural action"],
            clause_ids=clause_ids,
            amount=amount,
            limit=policy.auto_approve_limit,
        )

    # 2. Ruling must license the action
    if ruling is None:
        return deny("no ruling: actions require a ruling from the rule engine")
    if ruling.decision in (Decision.deny,):
        return deny(f"ruling is '{ruling.decision}'; no remedy action is licensed")
    if ruling.decision in (Decision.needs_fact, Decision.escalate):
        return deny(f"ruling is '{ruling.decision}'; resolve before acting")
    if ruling.remedy is None or action not in REMEDY_ACTIONS.get(ruling.remedy, set()):
        return deny(f"action '{action}' is not licensed by remedy '{ruling.remedy}'")

    # 3. Money checks
    if action in MONEY_ACTIONS:
        if amount is None:
            return deny("money action without an amount")
        if amount < 0:
            return deny("negative amount")
        if ruling.amount is not None and amount > ruling.amount:
            return deny(f"amount {amount} exceeds the ruled amount {ruling.amount}")
        if resource.max_amount is not None and amount > resource.max_amount:
            return deny(f"amount {amount} exceeds the resource maximum {resource.max_amount}")
        reasons.append(f"amount {amount} within ruled amount")

    # 4. Mode and authority
    if policy.mode == Mode.shadow:
        return needs_approval("shadow mode: actions are recorded, not executed")
    if policy.mode == Mode.assist:
        return needs_approval("assist mode: a human approves every action")
    if ruling.decision == Decision.needs_approval:
        return needs_approval("ruling requires approval")
    if amount is not None and amount > policy.auto_approve_limit:  # any action that moves value, incl. replacements
        return needs_approval(f"amount {amount} exceeds auto-approve limit {policy.auto_approve_limit}")

    reasons.append(f"licensed by rule {ruling.winning_rule_id} ({ruling.winning_clause_id})")
    return Authorization(
        decision=PDPDecision.allow,
        action=action,
        actor_id=actor.actor_id,
        reasons=reasons,
        clause_ids=clause_ids,
        amount=amount,
        limit=policy.auto_approve_limit,
    )
