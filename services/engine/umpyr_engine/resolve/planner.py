"""Plan generation with online verification (design v2 §4).

Two planners run per turn and are compared:

- historical: the structured case is embedded (fixed-order serialisation of category, intents, claim facts,
  disruption, party), the k nearest historical cases are retrieved on `cases.plan_embedding`, and their staff
  action sequences are aggregated into a consensus plan (steps present in >= half of the neighbours, ordered by
  median relative position). Mean similarity and agreement (mean Jaccard of neighbour sequences vs consensus)
  are kept: far or disagreeing neighbours mean a novel case.
- live: Haiku decomposes the intents into steps drawn only from the fixed staff-action vocabulary.

Plans are compared by step type and order, never by count: verification steps collapse to `verify`, evidence
requests to `evidence`, while remedy actions keep their verb because `issue_store_credit` vs `issue_refund` is a
material difference. History wins when both agree (or differ only in order); a divergence is a governance flag
(`planner_disagreement`) that queues an otherwise auto-approved money action.
"""

from __future__ import annotations

import logging
import statistics
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import models as m
from ..embeddings import EmbeddingProvider
from ..learn.vocab import STAFF_ACTIONS
from ..llm import LLMProvider
from ..llm.helpers import json_request, llm_json

log = logging.getLogger(__name__)

INTENTS = [
    "refund",
    "cancel",
    "rebook",
    "replacement",
    "return",
    "status",
    "timeline",
    "compensation",
    "complaint",
    "bag",
    "other",
]

REMEDY_STEPS = {
    "issue_replacement",
    "issue_refund",
    "issue_partial_refund",
    "issue_store_credit",
    "issue_travel_credit",
    "send_return_label",
    "authorize_return",
    "apply_restocking_fee",
    "cancel_order",
    "update_order",
    "rebook",
    "refund_bag_fee",
    "file_bag_report",
    "issue_bag_guarantee",
    "reimburse_expenses",
    "award_points",
    "provide_hotel_voucher",
    "provide_meal_voucher",
    "file_carrier_claim",
    "deny_request",
}
STEP_TYPE = {
    "verify_order": "verify",
    "verify_booking": "verify",
    "check_policy": "verify",
    "check_fare_rules": "verify",
    "check_flight_status": "verify",
    "confirm_cause": "verify",
    "request_photos": "evidence",
    "review_photos": "evidence",
    "request_receipt": "evidence",
    "request_report": "evidence",
    "provide_eta": "inform",
    "escalate_supervisor": "escalate",
}
NOISE = {"acknowledge", "close_ticket", "other"}
# history is "confident" (and may gate a divergence) only when enough neighbours agree on the remedy
CONFIDENT_MIN_K = 3
CONFIDENT_MIN_AGREEMENT = 0.5
CONFIDENT_MIN_REMEDY_SUPPORT = 0.6


def normalise_steps(actions: list[str]) -> list[str]:
    """Step types in order, consecutive duplicates collapsed, noise dropped; remedy verbs are kept verbatim."""
    out: list[str] = []
    for a in actions:
        if a in NOISE:
            continue
        s = a if a in REMEDY_STEPS else STEP_TYPE.get(a)
        if s is None:
            continue
        if not out or out[-1] != s:
            out.append(s)
    return out


_REASON_INTENTS = {
    "refund_request": ["refund"],
    "credit_request": ["refund"],
    "cancel_order": ["cancel"],
    "flight_cancelled": ["refund", "rebook"],
    "flight_delayed": ["refund", "compensation"],
    "schedule_change": ["rebook", "refund"],
    "damaged_on_arrival": ["replacement"],
    "defective": ["replacement"],
    "wrong_item": ["replacement"],
    "missing_item": ["replacement"],
    "change_of_mind": ["return"],
    "lifetime_guarantee": ["replacement"],
    "backorder": ["status"],
    "order_change": ["status"],
    "bag_delayed": ["bag"],
    "bag_damaged": ["bag", "compensation"],
    "bag_lost": ["bag", "compensation"],
    "bag_guarantee": ["bag", "compensation"],
    "seat_refund": ["refund"],
    "ancillary_refund": ["refund"],
    "loyalty_points": ["compensation"],
    "denied_boarding": ["compensation", "rebook"],
}
_REMEDY_INTENT = {
    "refund": "refund",
    "partial_refund": "refund",
    "replacement": "replacement",
    "reship": "replacement",
    "store_credit": "refund",
    "travel_credit": "refund",
    "rebook": "rebook",
    "points": "compensation",
    "voucher": "compensation",
    "expense_reimbursement": "compensation",
    "cancel_order": "cancel",
    "return_label": "return",
}


def intents_from_claim(reason: str | None, requested_remedy: str | None) -> list[str]:
    """Intents for historical cases, which were mined without an intents field."""
    out = list(_REASON_INTENTS.get(reason or "", []))
    r = _REMEDY_INTENT.get(requested_remedy or "")
    if r and r not in out:
        out.insert(0, r)
    return out or ["other"]


def structured_case_text(
    category: str | None,
    intents: list[str] | None,
    claim: dict[str, Any] | None,
    facts: dict[str, Any] | None,
    party: dict[str, Any] | None,
) -> str:
    """Fixed-order serialisation of the structured case, embedded for the historical planner."""
    claim = claim or {}
    facts = facts or {}
    fl = facts.get("flight") or {}
    bk = facts.get("booking") or {}
    bag = facts.get("bag") or {}
    item = facts.get("item") or {}
    parts = [
        f"category: {category or 'unknown'}",
        f"intents: {','.join(intents or []) or 'unknown'}",
        f"reason: {claim.get('reason') or 'unknown'}",
        f"requested: {claim.get('requested_remedy') or 'unknown'}",
    ]
    for label, val in (
        ("delay_minutes", fl.get("delay_minutes")),
        ("cause", fl.get("cause")),
        ("cancelled_by_airline", fl.get("is_cancelled_by_airline")),
        ("accepted_alternative", fl.get("customer_accepted_alternative")),
        ("customer_cancelled", bk.get("customer_cancelled")),
        ("fare_refundable", bk.get("is_refundable_fare")),
        ("bag_delay_days", bag.get("delay_days")),
        ("bag_damage", bag.get("damage_type")),
        ("bag_report_filed", bag.get("report_filed")),
        ("item_condition", item.get("condition")),
        ("photos", claim.get("has_photos")),
        ("receipt", claim.get("has_receipt")),
        ("unopened", claim.get("item_unopened")),
    ):
        if val is not None and val != "":
            parts.append(f"{label}: {val}")
    if party and party.get("pax_count"):
        parts.append(f"pax: {party['pax_count']} {','.join(party.get('pax_types') or [])}".rstrip())
    if claim.get("timeline_ask"):
        parts.append("asks: timeline")
    return " | ".join(parts)


def historical_case_text(case: m.Case) -> str:
    facts = case.facts or {}
    claim = facts.get("claim") or {}
    return structured_case_text(
        case.category_key, intents_from_claim(claim.get("reason"), claim.get("requested_remedy")), claim, facts, None
    )


def consensus(sequences: list[list[str]], min_support: float = 0.5) -> tuple[list[str], dict[str, float]]:
    n = len(sequences) or 1
    presence: Counter[str] = Counter()
    positions: dict[str, list[float]] = defaultdict(list)
    for seq in sequences:
        for i, a in enumerate(dict.fromkeys(seq)):
            presence[a] += 1
            positions[a].append(i / (len(seq) - 1) if len(seq) > 1 else 0.0)
    support = {a: round(c / n, 3) for a, c in presence.items()}
    steps = sorted([a for a, f in support.items() if f >= min_support], key=lambda a: statistics.median(positions[a]))
    return steps, support


def agreement(sequences: list[list[str]], steps: list[str]) -> float:
    if not sequences or not steps:
        return 0.0
    ref = set(steps)
    scores = [len(set(seq) & ref) / len(set(seq) | ref) for seq in sequences if seq or ref]
    return round(sum(scores) / len(scores), 3) if scores else 0.0


def backfill_plan_embeddings(
    session: Session, embedder: EmbeddingProvider, workspace_id: str, only_missing: bool = True
) -> int:
    """Embed the structured text of every historical case (local embedder: free). Returns the count embedded."""
    q = select(m.Case).where(m.Case.workspace_id == workspace_id)
    if only_missing:
        q = q.where(m.Case.plan_embedding.is_(None))
    cases = session.execute(q).scalars().all()
    if not cases:
        return 0
    texts = [historical_case_text(c) for c in cases]
    for i in range(0, len(cases), 256):
        emb = embedder.embed(texts[i : i + 256], step="plan.embed", workspace_id=workspace_id)
        for c, vec in zip(cases[i : i + 256], emb.vectors, strict=True):
            c.plan_embedding = vec
    session.commit()
    return len(cases)


def historical_plan(
    session: Session, embedder: EmbeddingProvider, workspace_id: str, case_text: str, k: int = 5
) -> dict[str, Any]:
    vec = embedder.embed([case_text], step="resolve.plan_embed", workspace_id=workspace_id, input_type="query").vectors[
        0
    ]
    rows = session.execute(
        select(m.Case, m.Case.plan_embedding.cosine_distance(vec).label("d"))
        .where(m.Case.workspace_id == workspace_id, m.Case.plan_embedding.isnot(None))
        .order_by("d")
        .limit(k)
    ).all()
    neighbours = []
    seqs: list[list[str]] = []
    for case, d in rows:
        rec = case.record or {}
        raw = [a.get("action") for a in (rec.get("staff_actions") or []) if isinstance(a, dict)]
        seq = normalise_steps([a for a in raw if a])
        seqs.append(seq)
        neighbours.append(
            {
                "ticket_id": case.ticket_id,
                "similarity": round(1 - float(d), 3),
                "category_key": case.category_key,
                "steps": seq,
                "outcome": rec.get("outcome"),
            }
        )
    steps, support = consensus(seqs)
    sims = [n["similarity"] for n in neighbours]
    agr = agreement(seqs, steps)
    remedy_support = [support.get(st, 0.0) for st in steps if st in REMEDY_STEPS]
    confident = (
        len(neighbours) >= CONFIDENT_MIN_K
        and agr >= CONFIDENT_MIN_AGREEMENT
        and bool(remedy_support)
        and min(remedy_support) >= CONFIDENT_MIN_REMEDY_SUPPORT
    )
    return {
        "k": len(neighbours),
        "steps": steps,
        "support": support,
        "agreement": agr,
        "mean_similarity": round(sum(sims) / len(sims), 3) if sims else None,
        "confident": confident,
        "neighbours": neighbours,
    }


LIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {"type": "array", "items": {"type": "string", "enum": STAFF_ACTIONS}},
        "rationale": {"type": "string"},
    },
    "required": ["steps", "rationale"],
    "additionalProperties": False,
}
LIVE_SYSTEM = """You plan how a merchant's support agent should handle one customer case. Decompose the customer's intents into
an ordered list of 2 to 6 steps using ONLY the action vocabulary provided; never invent actions. Start with the
verification the case needs (verify_order / verify_booking, check_policy or check_fare_rules, confirm_cause for
flight disruptions), add any request_* step for evidence the case still lacks, then exactly the remedy steps the
facts support (for a cancelled or long-delayed flight where the customer declines travel: issue_refund; for a
non-refundable voluntary cancellation: issue_travel_credit; damaged item with photos: issue_replacement). Do not
include acknowledge or close_ticket. The rationale is at most 20 words."""


def live_plan(
    llm: LLMProvider, workspace_id: str, case_text: str, procedure_steps: list[str], record_summary: str | None
) -> dict[str, Any]:
    user = (
        f"Case: {case_text}\nMerchant record: {record_summary or 'not found yet'}\n"
        f"Approved procedure steps (names only, for context): {procedure_steps}\n"
        f"Action vocabulary: {', '.join(a for a in STAFF_ACTIONS if a not in NOISE)}"
    )
    res = (
        llm_json(
            llm,
            json_request(
                step="resolve.plan",
                system=LIVE_SYSTEM,
                user=user,
                schema=LIVE_SCHEMA,
                max_tokens=600,
                workspace_id=workspace_id,
            ),
        )
        or {}
    )
    raw = [s for s in (res.get("steps") or []) if s in STAFF_ACTIONS][:8]
    return {"raw": raw, "steps": normalise_steps(raw), "rationale": res.get("rationale") or ""}


REMEDY_TO_STEPS = {
    "refund": {"issue_refund"},
    "partial_refund": {"issue_partial_refund", "issue_refund"},
    "replacement": {"issue_replacement"},
    "reship": {"issue_replacement"},
    "repair": {"issue_replacement"},
    "return_label": {"send_return_label", "authorize_return"},
    "store_credit": {"issue_store_credit"},
    "travel_credit": {"issue_travel_credit"},
    "rebook": {"rebook"},
    "points": {"award_points"},
    "voucher": {"provide_hotel_voucher", "provide_meal_voucher"},
    "expense_reimbursement": {"reimburse_expenses"},
    "bag_delivery": {"file_bag_report"},
    "cancel_order": {"cancel_order"},
}


def history_backs_ruling(plan: dict[str, Any], decision: str | None, remedy: str | None) -> bool | None:
    """Does the confident historical consensus support the ruling's remedy? None when history is not confident
    or has no remedy step. A ruling that contradicts confident precedent is what the planner gate queues."""
    hist = plan.get("historical") or {}
    if not hist.get("confident"):
        return None
    hist_remedies = {s for s in hist.get("steps", []) if s in REMEDY_STEPS}
    if not hist_remedies:
        return None
    if decision == "deny":
        return "deny_request" in hist_remedies
    return bool(REMEDY_TO_STEPS.get(remedy or "", set()) & hist_remedies)


def compare(historical: list[str], live: list[str]) -> dict[str, Any]:
    """By step type and order, never by count. Only a different set of remedy steps is a divergence that gates;
    different verification or evidence steps are a `minor_diff` (flagged, never queued)."""
    h_rem = [s for s in historical if s in REMEDY_STEPS]
    l_rem = [s for s in live if s in REMEDY_STEPS]
    if not historical and not live:
        kind = "none"
    elif not historical:
        kind = "no_history"
    elif not live:
        kind = "no_live"
    elif historical == live:
        kind = "agree"
    elif set(historical) == set(live):
        kind = "order_differs"
    elif set(h_rem) != set(l_rem):
        kind = "diverge"
    else:
        kind = "minor_diff"
    return {
        "kind": kind,
        "missing_in_live": [s for s in historical if s not in live],
        "extra_in_live": [s for s in live if s not in historical],
        "remedies": {"historical": h_rem, "live": l_rem},
    }


def plan_case(
    session: Session,
    llm: LLMProvider,
    embedder: EmbeddingProvider,
    workspace_id: str,
    *,
    category: str | None,
    intents: list[str],
    claim: dict[str, Any],
    facts: dict[str, Any],
    party: dict[str, Any] | None,
    procedure_steps: list[str],
    record_summary: str | None,
    k: int,
    novelty_min_similarity: float,
    agreement_min: float,
) -> dict[str, Any]:
    text = structured_case_text(category, intents, claim, facts, party)
    hist = historical_plan(session, embedder, workspace_id, text, k=k)
    try:
        live = live_plan(llm, workspace_id, text, procedure_steps, record_summary)
    except Exception as e:  # the live planner is advisory: never fail the turn on it
        log.warning("live planner failed: %s", e)
        live = {"raw": [], "steps": [], "rationale": f"planner unavailable: {type(e).__name__}"}
    cmp = compare(hist["steps"], live["steps"])
    novel = (
        hist["k"] == 0
        or (hist["mean_similarity"] is not None and hist["mean_similarity"] < novelty_min_similarity)
        or hist["agreement"] < agreement_min
    )
    if (
        hist["steps"]
        and not novel
        and (cmp["kind"] in ("agree", "order_differs", "minor_diff", "no_live") or not hist.get("confident"))
    ):
        chosen, steps = "historical", hist["steps"]
    elif live["steps"]:
        chosen, steps = "live", live["steps"]
    elif hist["steps"]:
        chosen, steps = "historical", hist["steps"]
    else:
        chosen, steps = "none", []
    return {
        "case_text": text,
        "historical": hist,
        "live": live,
        "comparison": cmp,
        "novel": novel,
        "chosen": chosen,
        "steps": steps,
        "disagreement": cmp["kind"] == "diverge" and bool(hist.get("confident")),  # only confident history gates
    }
