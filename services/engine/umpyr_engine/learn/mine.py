"""Mine: Haiku turns each masked ticket thread into a structured case record.

Output per ticket: summary, claim facts (ontology `claim.*`), item hint, ordered staff actions,
outcome, category hint. Code then joins the merchant order record (by order_id) and derives the
date facts so the case holds a complete ontology Facts bundle. Runs on the Batch API.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db.models import Case, OrderRecord, Ticket
from ..llm import LLMProvider
from ..llm.helpers import json_request, llm_json_batch, llm_json_concurrent
from .vocab import CLAIM_REASONS, DECISIONS, FAULTS, REMEDIES, STAFF_ACTIONS

log = logging.getLogger(__name__)

YNU = {"type": "string", "enum": ["yes", "no", "unknown"]}
INT_U = {"type": "integer", "description": "-1 when unknown"}
NUM_U = {"type": "number", "description": "-1 when unknown"}

# No nullable/union types: the API caps union-typed parameters. Unknowns use sentinels ("unknown", -1, "").
MINE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "One masked sentence: who asked for what and what happened"},
        "category_hint": {"type": "string", "description": "2-4 word label for the kind of issue"},
        "order_id": {"type": "string", "description": "Order number / confirmation code, or empty string"},
        "item_hint": {"type": "string", "description": "Product or service the issue is about, or empty string"},
        "claim": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "enum": CLAIM_REASONS},
                "requested_remedy": {"type": "string", "enum": [*REMEDIES, "unknown"]},
                "requested_amount": NUM_U,
                "has_photos": YNU,
                "has_receipt": YNU,
                "reported_within_days": INT_U,
                "item_unopened": YNU,
                "item_used": YNU,
                "wants_credit_instead_of_refund": YNU,
                "description": {"type": "string"},
            },
            "required": [
                "reason",
                "requested_remedy",
                "requested_amount",
                "has_photos",
                "has_receipt",
                "reported_within_days",
                "item_unopened",
                "item_used",
                "wants_credit_instead_of_refund",
                "description",
            ],
            "additionalProperties": False,
        },
        "airline": {
            "type": "object",
            "description": "Only meaningful for airline tickets; use unknown/-1 otherwise",
            "properties": {
                "delay_minutes": INT_U,
                "cause": {"type": "string", "enum": ["controllable", "uncontrollable", "unknown"]},
                "is_cancelled_by_airline": YNU,
                "customer_accepted_alternative": YNU,
                "customer_cancelled": YNU,
                "hours_since_purchase": INT_U,
                "bag_minutes_to_carousel": INT_U,
                "bag_report_filed": YNU,
                "bag_report_filed_within_hours": INT_U,
                "bag_delay_days": INT_U,
                "bag_damage_type": {
                    "type": "string",
                    "description": "structural, wheel, handle, zipper, cosmetic, contents, none, or empty",
                },
                "interim_expenses": NUM_U,
                "overnight_required": YNU,
            },
            "required": [
                "delay_minutes",
                "cause",
                "is_cancelled_by_airline",
                "customer_accepted_alternative",
                "customer_cancelled",
                "hours_since_purchase",
                "bag_minutes_to_carousel",
                "bag_report_filed",
                "bag_report_filed_within_hours",
                "bag_delay_days",
                "bag_damage_type",
                "interim_expenses",
                "overnight_required",
            ],
            "additionalProperties": False,
        },
        "staff_actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": STAFF_ACTIONS},
                    "detail": {"type": "string"},
                    "amount": NUM_U,
                },
                "required": ["action", "detail", "amount"],
                "additionalProperties": False,
            },
        },
        "outcome": {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": DECISIONS},
                "remedy": {"type": "string", "enum": [*REMEDIES, "unknown"]},
                "amount": NUM_U,
                "fault": {"type": "string", "enum": [*FAULTS, "unknown"]},
            },
            "required": ["decision", "remedy", "amount", "fault"],
            "additionalProperties": False,
        },
    },
    "required": ["summary", "category_hint", "order_id", "item_hint", "claim", "airline", "staff_actions", "outcome"],
    "additionalProperties": False,
}


def _clean(value):
    """Sentinels -> None: 'unknown'/'' -> None, 'yes'/'no' -> bool, -1 -> None."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if value == "yes":
        return True
    if value == "no":
        return False
    if value in ("unknown", ""):
        return None
    if isinstance(value, int | float) and not isinstance(value, bool) and value == -1:
        return None
    return value


SYSTEM = f"""You extract structured case records from customer-support threads for Umpyr. Text is already PII-masked
([NAME], [EMAIL], [PHONE], [ADDRESS]); keep those tokens, never guess identities.

Extract only what the thread states or clearly implies. Use null when unknown.
- claim.reason: the primary reason, one of {", ".join(CLAIM_REASONS)}. The reason is what the customer ASKS FOR:
  a customer who wants to cancel a backordered item is cancel_order (not backorder); backorder is only for status/ETA questions.
  Airline reasons: flight_cancelled = the airline cancelled the flight; flight_delayed = the flight operated late
  (any request arising from it: refund, meals, hotel); schedule_change = the airline moved the schedule before travel;
  refund_request = the CUSTOMER chose to cancel and wants money or credit back; bag_guarantee = the bag was late to the
  carousel / 20-minute guarantee (bag arrived the same day); bag_delayed = bag delivered hours or days later;
  bag_lost = never returned; bag_damaged = physical damage; seat_refund = paid seat/upgrade fee; loyalty_points = miles/points.
- Unknown values: "unknown" for yes/no fields, -1 for numbers, "" for strings. Never guess.
- claim facts: has_photos = customer provided or attached photos at any point in the thread; has_receipt = proof of
  purchase available (an order number from the merchant counts as proof); reported_within_days = days between
  delivery/arrival and the first message if stated; item_unopened / item_used as stated.
- staff_actions: every action the staff took, in order, using the fixed vocabulary {", ".join(STAFF_ACTIONS)}.
  Include amounts when money moved.
- outcome: what the staff finally gave: decision approve (full remedy), partial (reduced), deny, needs_approval
  (sent for authorization and then decided), escalate (handed to a human with no decision in the thread);
  remedy and amount as stated; fault = who the staff treated as responsible.
- summary: one masked sentence. category_hint: a short label such as "damaged on arrival" or "bag delayed".
"""


def _user(ticket: Ticket) -> str:
    msgs = "\n\n".join(f"[{m['role'].upper()} {m.get('at') or ''}]\n{m['text']}" for m in ticket.thread)
    return (
        f"Ticket {ticket.ticket_id} ({ticket.channel}) created {ticket.ticket_created_at}\n"
        f"Subject: {ticket.subject}\nOrder/booking reference: {ticket.order_id or 'not provided'}\n"
        f"Recorded staff actions: {', '.join(ticket.agent_actions) or 'none'}\n"
        f"Recorded resolution: {ticket.resolution or 'none'}; amount: {ticket.amount}\n\n{msgs}"
    )


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def hours_to_resolve(ticket: Ticket) -> float | None:
    stamps = [d for d in (_parse_dt(m.get("at")) for m in ticket.thread) if d]
    if len(stamps) >= 2:
        return round((max(stamps) - min(stamps)).total_seconds() / 3600, 2)
    return None


def build_facts(mined: dict[str, Any], order: dict[str, Any] | None, ticket: Ticket, kind: str) -> dict[str, Any]:
    """Join mined claim facts with the merchant record into an ontology Facts dict (shared builder)."""
    from ..resolve.facts import build_facts as _build

    at = ticket.ticket_created_at.replace(tzinfo=None) if ticket.ticket_created_at else datetime.now()
    return _build(
        mined.get("claim") or {}, mined.get("airline") or {}, order, at, kind, item_hint=mined.get("item_hint")
    )


def mine_workspace(
    session: Session,
    llm: LLMProvider,
    workspace_id: str,
    *,
    kind: str = "order",
    replace: bool = True,
    limit: int | None = None,
    use_batch: bool = True,
) -> dict[str, Any]:
    tickets = list(
        session.execute(select(Ticket).where(Ticket.workspace_id == workspace_id).order_by(Ticket.ticket_id)).scalars()
    )
    if limit:
        tickets = tickets[:limit]
    orders = {
        o.order_id: o.data
        for o in session.execute(select(OrderRecord).where(OrderRecord.workspace_id == workspace_id)).scalars()
    }
    reqs = [
        json_request(
            step="mine", system=SYSTEM, user=_user(t), schema=MINE_SCHEMA, max_tokens=1500, workspace_id=workspace_id
        )
        for t in tickets
    ]
    log.info("mining %d tickets", len(reqs))
    results = llm_json_batch(llm, reqs) if use_batch else llm_json_concurrent(llm, reqs)
    if replace:
        session.execute(delete(Case).where(Case.workspace_id == workspace_id))
    n = failed = 0
    for t, res in zip(tickets, results, strict=True):
        if not res:
            failed += 1
            continue
        res = _clean(res)
        order = orders.get(t.order_id or res.get("order_id") or "")
        facts = build_facts(res, order, t, kind)
        actions = [
            {"seq": i + 1, "action": a["action"], "detail": a.get("detail"), "amount": a.get("amount")}
            for i, a in enumerate(res.get("staff_actions") or [])
        ]
        outcome = res.get("outcome") or {}
        record = {
            "case_id": f"case-{t.ticket_id}",
            "ticket_id": t.ticket_id,
            "summary": res.get("summary"),
            "category_hint": res.get("category_hint"),
            "item_hint": res.get("item_hint"),
            "first_message": next((m_["text"] for m_ in t.thread if m_.get("role") == "customer"), "")[:800],
            "staff_actions": actions,
            "outcome": outcome,
            "opened_at": t.ticket_created_at.isoformat() if t.ticket_created_at else None,
            "is_synthetic": t.is_synthetic,
        }
        session.add(
            Case(
                workspace_id=workspace_id,
                ticket_id=t.ticket_id,
                record=json.loads(json.dumps(record, default=str)),
                facts=json.loads(json.dumps(facts, default=str)),
                summary=res.get("summary"),
                hours_to_resolve=hours_to_resolve(t),
                is_synthetic=t.is_synthetic,
            )
        )
        n += 1
    session.commit()
    return {"cases": n, "failed": failed}
