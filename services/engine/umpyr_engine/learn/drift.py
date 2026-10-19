"""Policy vs practice: replay the active rule pack over mined cases.

- Mismatch between what policy decides and what staff did -> drift finding (never a training label).
- Consistent staff pattern where policy has no answer (escalate / needs_fact) -> unwritten-rule candidate.
- Gold cases = policy-decided and consistent; they get a train/test split for the eval harness.
"""

from __future__ import annotations

import json
import logging
import random
from collections import defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from umpyr_rules import RulePack, evaluate

from ..db.models import Case, DriftFinding
from ..db.models import RulePack as RulePackRow

log = logging.getLogger(__name__)

APPROVE_FAMILY = {"approve", "partial"}


def _family(decision: str | None) -> str | None:
    if decision in APPROVE_FAMILY:
        return "approve"
    if decision == "deny":
        return "deny"
    return None


def compare(policy, staff: dict[str, Any]) -> str | None:
    """Return a drift reason, or None when the staff outcome is consistent with the policy ruling."""
    pd = str(policy.decision)
    sd = staff.get("decision")
    if pd in ("needs_approval", "escalate", "needs_fact") or sd in (None, "unknown", "escalate", "needs_approval"):
        return None
    pf, sf = _family(pd), _family(sd)
    if pf and sf and pf != sf:
        return f"policy {pd}/{policy.remedy} ({policy.winning_rule_id}) but staff {sd}/{staff.get('remedy')}"
    if pf == "approve":
        pr, sr = str(policy.remedy or ""), str(staff.get("remedy") or "")
        money = {"refund", "partial_refund", "store_credit", "travel_credit", "voucher"}
        if pr != sr and not (pr in ("refund", "partial_refund") and sr in ("refund", "partial_refund")):
            if pr in money and sr in money:
                return f"policy remedy {pr} but staff gave {sr} ({policy.winning_rule_id})"
            if pr != sr:
                return f"policy remedy {pr} but staff gave {sr} ({policy.winning_rule_id})"
        if policy.amount is not None and staff.get("amount") not in (None, ""):
            try:
                sa = Decimal(str(staff["amount"]))
            except Exception:  # noqa: BLE001
                return None
            if sa > policy.amount * Decimal("1.05") + Decimal("1"):
                return f"staff amount {sa} exceeds policy amount {policy.amount} ({policy.winning_rule_id})"
    return None


def replay(session: Session, workspace_id: str, *, seed: int = 0) -> dict[str, Any]:
    row = (
        session.execute(
            select(RulePackRow).where(RulePackRow.workspace_id == workspace_id, RulePackRow.status == "active")
        )
        .scalars()
        .first()
    )
    if row is None:
        raise RuntimeError("no active rule pack")
    pack = RulePack.model_validate(row.pack)
    cases = list(
        session.execute(select(Case).where(Case.workspace_id == workspace_id).order_by(Case.ticket_id)).scalars()
    )
    session.execute(delete(DriftFinding).where(DriftFinding.workspace_id == workspace_id))

    drift_n = 0
    undecided: dict[tuple, list[Case]] = defaultdict(list)
    decisions: dict[str, int] = defaultdict(int)
    gold: list[Case] = []
    for c in cases:
        facts = c.facts or {}
        ruling = evaluate(pack, facts, category=None)
        staff = (c.record or {}).get("outcome") or {}
        decisions[str(ruling.decision)] += 1
        c.record = {
            **(c.record or {}),
            "policy": {
                "decision": str(ruling.decision),
                "remedy": ruling.remedy,
                "amount": str(ruling.amount) if ruling.amount is not None else None,
                "rule_id": ruling.winning_rule_id,
                "clause_ids": ruling.clause_ids,
                "missing_facts": ruling.missing_facts,
            },
        }
        reason = compare(ruling, staff)
        c.is_drift = reason is not None
        c.drift_reason = reason
        if reason:
            drift_n += 1
            session.add(
                DriftFinding(
                    workspace_id=workspace_id,
                    case_id=c.id,
                    kind="drift",
                    category_key=c.category_key,
                    rule_id=ruling.winning_rule_id,
                    clause_id=ruling.winning_clause_id,
                    staff_outcome=staff,
                    policy_outcome={
                        "decision": str(ruling.decision),
                        "remedy": ruling.remedy,
                        "amount": str(ruling.amount) if ruling.amount is not None else None,
                    },
                    pattern=reason,
                    status="open",
                )
            )
        if str(ruling.decision) in ("escalate", "needs_fact"):
            key = (c.category_key, facts.get("claim", {}).get("reason"), staff.get("decision"), staff.get("remedy"))
            undecided[key].append(c)
        c.is_gold = (str(ruling.decision) in ("approve", "partial", "deny")) and not c.is_drift
        c.split = "held" if c.is_drift else None
        if c.is_gold:
            gold.append(c)
        session.add(c)

    # unwritten-rule candidates: consistent staff practice where policy is silent
    by_bucket: dict[tuple, list[Case]] = defaultdict(list)
    for (cat, reason, _d, _r), cs in undecided.items():
        by_bucket[(cat, reason)].extend(cs)
    unwritten = 0
    for (cat, reason), cs in by_bucket.items():
        if len(cs) < 5:
            continue
        outcomes = defaultdict(int)
        for c in cs:
            o = (c.record or {}).get("outcome") or {}
            outcomes[(o.get("decision"), o.get("remedy"))] += 1
        (dec, rem), n = max(outcomes.items(), key=lambda kv: kv[1])
        if n / len(cs) >= 0.8:
            unwritten += 1
            session.add(
                DriftFinding(
                    workspace_id=workspace_id,
                    kind="unwritten_rule",
                    category_key=cat,
                    staff_outcome={"decision": dec, "remedy": rem},
                    policy_outcome={"decision": "no rule"},
                    pattern=f"For claim.reason={reason} in {cat}, staff consistently decide {dec}/{rem} ({n}/{len(cs)} cases) but no compiled clause governs it.",
                    support=n,
                    status="open",
                )
            )

    rng = random.Random(seed)
    rng.shuffle(gold)
    cut = int(len(gold) * 0.8)
    for i, c in enumerate(gold):
        c.split = "train" if i < cut else "test"
        session.add(c)
    session.commit()
    return {
        "cases": len(cases),
        "policy_decisions": dict(decisions),
        "drift_findings": drift_n,
        "unwritten_rule_candidates": unwritten,
        "gold": len(gold),
        "split": {"train": cut, "test": len(gold) - cut, "held": drift_n},
        "rule_pack_version": pack.version,
    }


def score_against_truth(session: Session, workspace_id: str, truth: list[dict]) -> dict[str, Any]:
    """Sandbox only: how well did mining + replay recover the hidden ground truth?"""
    by_id = {t["ticket_id"]: t for t in truth}
    cases = list(session.execute(select(Case).where(Case.workspace_id == workspace_id)).scalars())
    tp = fp = fn = 0
    reason_ok = decision_ok = n = 0
    for c in cases:
        t = by_id.get(c.ticket_id)
        if not t:
            continue
        n += 1
        if c.is_drift and t["is_drift"]:
            tp += 1
        elif c.is_drift:
            fp += 1
        elif t["is_drift"]:
            fn += 1
        if (c.facts or {}).get("claim", {}).get("reason") == t["claim_reason"]:
            reason_ok += 1
        if ((c.record or {}).get("policy") or {}).get("decision") == t["policy"]["decision"]:
            decision_ok += 1
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    return {
        "scored": n,
        "claim_reason_accuracy": round(reason_ok / n, 3) if n else None,
        "policy_decision_agreement": round(decision_ok / n, 3) if n else None,
        "drift_precision": round(prec, 3) if prec is not None else None,
        "drift_recall": round(rec, 3) if rec is not None else None,
        "drift_tp_fp_fn": [tp, fp, fn],
    }


def dump_cases(session: Session, workspace_id: str) -> list[dict]:
    return [
        {
            "ticket_id": c.ticket_id,
            "category_key": c.category_key,
            "record": c.record,
            "facts": c.facts,
            "summary": c.summary,
            "hours_to_resolve": float(c.hours_to_resolve) if c.hours_to_resolve is not None else None,
            "is_drift": c.is_drift,
            "drift_reason": c.drift_reason,
            "is_gold": c.is_gold,
            "split": c.split,
        }
        for c in session.execute(
            select(Case).where(Case.workspace_id == workspace_id).order_by(Case.ticket_id)
        ).scalars()
    ]


def load_cases(session: Session, workspace_id: str, rows: list[dict]) -> int:
    session.execute(delete(Case).where(Case.workspace_id == workspace_id))
    for r in rows:
        session.add(
            Case(
                workspace_id=workspace_id,
                ticket_id=r["ticket_id"],
                category_key=r.get("category_key"),
                record=r["record"],
                facts=r["facts"],
                summary=r.get("summary"),
                hours_to_resolve=r.get("hours_to_resolve"),
                is_drift=bool(r.get("is_drift")),
                drift_reason=r.get("drift_reason"),
                is_gold=bool(r.get("is_gold")),
                split=r.get("split"),
            )
        )
    session.commit()
    return len(rows)


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)
