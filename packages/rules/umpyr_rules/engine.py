"""Deterministic evaluation of a RulePack over a fact bundle.

Algorithm
1. Select applicable rules (enabled, category scope, claim.reason scope).
2. For each applicable rule, list missing `requires` facts. A rule with missing
   facts cannot fire.
3. Evaluate `when` for the remaining rules. Fired rules compute their effect
   amount (Decimal, rounded to cents).
4. Precedence: sort fired rules by (tier rank, -priority, rule_id). The first is
   the winner. If another fired rule shares the winner's tier and priority but
   disagrees on decision or remedy, that is a conflict -> decision `escalate`.
5. If nothing fired and some applicable rule had missing facts -> `needs_fact`
   listing the missing facts of the highest-precedence rules that could not be
   evaluated (one focused question at a time, not every fact in the pack).
   If nothing fired and nothing is missing -> `escalate` with explanation
   "no rule matched" (we never invent a ruling).

Same facts + same pack -> same Ruling. There is no clock or randomness here;
derived date fields (days_since_delivery) are computed by the caller.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from umpyr_ontology import Decision
from umpyr_ontology.enums import TIER_RANK

from .jsonlogic import JsonLogicError, apply, collect_vars, get_var
from .model import Check, Rule, RulePack, Ruling

CENTS = Decimal("0.01")


def _applicable(rule: Rule, facts: dict[str, Any], category: str | None) -> bool:
    if not rule.enabled:
        return False
    if rule.categories and (category is None or category not in rule.categories):
        return False
    if rule.claim_reasons:
        reason = get_var(facts, "claim.reason")
        if reason not in rule.claim_reasons:
            return False
    return True


def _amount(rule: Rule, facts: dict[str, Any]) -> Decimal | None:
    expr = rule.effect.amount
    if expr is None:
        return None
    value = apply(expr, facts)
    if value is None:
        return None
    if isinstance(value, bool):
        raise JsonLogicError("amount evaluated to a boolean")
    amt = Decimal(str(value)) if not isinstance(value, Decimal) else value
    if amt < 0:
        amt = Decimal(0)
    return amt.quantize(CENTS, rounding=ROUND_HALF_UP)


def _field_values(rule: Rule, facts: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for p in sorted(collect_vars(rule.when) | set(rule.requires)):
        if "." in p:
            v = get_var(facts, p)
            out[p] = v
    return out


def evaluate(pack: RulePack, facts: dict[str, Any], category: str | None = None) -> Ruling:
    checks: list[Check] = []
    fired: list[tuple[Check, Rule]] = []
    missing_all: set[str] = set()
    missing_top: tuple[int, int] | None = None  # (tier rank, -priority) of the first rules with missing facts

    for rule in sorted(pack.rules, key=lambda r: (TIER_RANK[r.tier], -r.priority, r.rule_id)):
        applicable = _applicable(rule, facts, category)
        check = Check(
            rule_id=rule.rule_id,
            clause_id=rule.clause_id,
            tier=rule.tier,
            priority=rule.priority,
            applicable=applicable,
            fields=_field_values(rule, facts) if applicable else {},
        )
        if not applicable:
            checks.append(check)
            continue
        missing = [p for p in rule.requires if get_var(facts, p) is None]
        if missing:
            check.missing = missing
            level = (TIER_RANK[rule.tier], -rule.priority)
            if missing_top is None or level == missing_top:
                missing_top = level
                missing_all.update(missing)
            checks.append(check)
            continue
        try:
            result = bool(apply(rule.when, facts))
            if result:
                check.fired = True
                check.effect = rule.effect
                check.amount = _amount(rule, facts)
                fired.append((check, rule))
        except JsonLogicError as e:
            check.error = str(e)
        checks.append(check)

    version = pack.version
    if not fired:
        if missing_all:
            return Ruling(
                decision=Decision.needs_fact,
                missing_facts=sorted(missing_all),
                checks=checks,
                rule_pack_version=version,
                explanation="Required facts are missing: " + ", ".join(sorted(missing_all)),
            )
        return Ruling(
            decision=Decision.escalate,
            checks=checks,
            rule_pack_version=version,
            explanation="No compiled rule matched these facts; a human must decide.",
        )

    # fired is already in precedence order because we iterated rules sorted.
    win_check, win_rule = fired[0]
    conflicts: list[str] = []
    for _, rule in fired[1:]:
        same_level = TIER_RANK[rule.tier] == TIER_RANK[win_rule.tier] and rule.priority == win_rule.priority
        if same_level and (
            rule.effect.decision != win_rule.effect.decision or rule.effect.remedy != win_rule.effect.remedy
        ):
            conflicts.append(
                f"{win_rule.rule_id} ({win_rule.effect.decision}/{win_rule.effect.remedy}) vs "
                f"{rule.rule_id} ({rule.effect.decision}/{rule.effect.remedy}) "
                f"at tier {rule.tier} priority {rule.priority}"
            )

    fired_ids = [r.rule_id for _, r in fired]
    clause_ids: list[str] = []
    for _, r in fired:
        if r.clause_id not in clause_ids:
            clause_ids.append(r.clause_id)

    if conflicts:
        return Ruling(
            decision=Decision.escalate,
            winning_rule_id=None,
            fired_rule_ids=fired_ids,
            clause_ids=clause_ids,
            conflicts=conflicts,
            checks=checks,
            rule_pack_version=version,
            explanation="Conflicting rules at the same precedence level; escalated for human decision.",
        )

    effect = win_rule.effect
    # A fired rule that still lacks facts for its *amount* yields needs_fact rather than a null amount.
    if (
        effect.decision in (Decision.approve, Decision.partial)
        and effect.amount is not None
        and win_check.amount is None
    ):
        amount_vars = sorted(p for p in collect_vars(effect.amount) if get_var(facts, p) is None)
        return Ruling(
            decision=Decision.needs_fact,
            winning_rule_id=win_rule.rule_id,
            winning_clause_id=win_rule.clause_id,
            tier=win_rule.tier,
            fired_rule_ids=fired_ids,
            clause_ids=clause_ids,
            missing_facts=amount_vars,
            checks=checks,
            rule_pack_version=version,
            explanation="Rule matched but the amount needs facts: " + ", ".join(amount_vars),
        )

    return Ruling(
        decision=effect.decision,
        remedy=effect.remedy,
        amount=win_check.amount,
        fault=effect.fault,
        winning_rule_id=win_rule.rule_id,
        winning_clause_id=win_rule.clause_id,
        tier=win_rule.tier,
        fired_rule_ids=fired_ids,
        clause_ids=clause_ids,
        checks=checks,
        rule_pack_version=version,
        explanation=effect.note or win_rule.description or f"Rule {win_rule.rule_id} applied.",
    )
