"""Runtime tools. Each is a plain function; the runtime (runtime.py) orchestrates them in a fixed order.

LLM-backed: classify_llm (fallback below the router threshold), extract_facts, draft_reply, supervise.
Deterministic: classify_router, find_neighbours, get_procedure, lookup_record, rule (umpyr_rules), authorize (PDP).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import models as m
from ..embeddings import EmbeddingProvider
from ..learn.router import THRESHOLD, load_router
from ..learn.vocab import CLAIM_REASONS, REMEDIES
from ..llm import LLMProvider
from ..llm.helpers import json_request, llm_json

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- classify
CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "category_key": {"type": "string"},
        "confidence": {"type": "number"},
        "multi_issue": {"type": "boolean"},
        "secondary_category_key": {"type": "string", "description": "empty unless multi_issue"},
        "rationale": {"type": "string"},
    },
    "required": ["category_key", "confidence", "multi_issue", "secondary_category_key", "rationale"],
    "additionalProperties": False,
}
CLASSIFY_SYSTEM = """You classify a customer's support message into exactly one of the merchant's approved categories.
Return the category_key from the list, a confidence 0-1, and whether the message raises more than one distinct issue
(multi_issue, with the secondary category). Choose by what the customer is asking for, not by the words used."""


def classify_router(
    session: Session, embedder: EmbeddingProvider, workspace_id: str, text: str
) -> tuple[str | None, float, dict[str, float], list[float] | None]:
    router = load_router(session, workspace_id)
    if router is None:
        return None, 0.0, {}, None
    vec = embedder.embed([text], step="resolve.embed", workspace_id=workspace_id, input_type="query").vectors[0]
    key, conf, probs = router.predict([vec])[0]
    return key, conf, probs, vec


def classify_llm(llm: LLMProvider, workspace_id: str, text: str, categories: list[m.Category]) -> dict[str, Any]:
    listing = "\n".join(f"- {c.key}: {c.name} - {c.description}" for c in categories)
    user = f"Categories:\n{listing}\n\nCustomer message:\n{text}"
    res = (
        llm_json(
            llm,
            json_request(
                step="resolve.classify",
                system=CLASSIFY_SYSTEM,
                user=user,
                schema=CLASSIFY_SCHEMA,
                max_tokens=300,
                workspace_id=workspace_id,
            ),
        )
        or {}
    )
    keys = {c.key for c in categories}
    if res.get("category_key") not in keys:
        res["category_key"] = next(iter(keys), None)
        res["confidence"] = 0.3
    return res


# ---------------------------------------------------------------- neighbours
def find_neighbours(session: Session, workspace_id: str, vec: list[float] | None, k: int = 3) -> list[dict[str, Any]]:
    if vec is None:
        return []
    rows = session.execute(
        select(m.Case, m.Case.embedding.cosine_distance(vec).label("d"))
        .where(m.Case.workspace_id == workspace_id, m.Case.embedding.isnot(None))
        .order_by("d")
        .limit(k)
    ).all()
    out = []
    for case, d in rows:
        rec = case.record or {}
        out.append(
            {
                "ticket_id": case.ticket_id,
                "similarity": round(1 - float(d), 3),
                "category_key": case.category_key,
                "summary": case.summary,
                "staff_outcome": rec.get("outcome"),
                "policy_outcome": rec.get("policy"),
                "is_drift": bool(case.is_drift),
                "drift_reason": case.drift_reason,
                "hours_to_resolve": float(case.hours_to_resolve) if case.hours_to_resolve is not None else None,
            }
        )
    return out


# ---------------------------------------------------------------- procedure
def get_procedure(session: Session, workspace_id: str, category_key: str | None) -> m.Procedure | None:
    if not category_key:
        return None
    return (
        session.execute(
            select(m.Procedure)
            .where(
                m.Procedure.workspace_id == workspace_id,
                m.Procedure.category_key == category_key,
                m.Procedure.status == "approved",
            )
            .order_by(m.Procedure.version.desc())
        )
        .scalars()
        .first()
    )


# ---------------------------------------------------------------- lookup
def lookup_record(session: Session, workspace_id: str, reference: str | None) -> dict[str, Any] | None:
    if not reference:
        return None
    row = (
        session.execute(
            select(m.OrderRecord).where(m.OrderRecord.workspace_id == workspace_id, m.OrderRecord.order_id == reference)
        )
        .scalars()
        .first()
    )
    return row.data if row else None


def stand_in_record(
    session: Session,
    workspace_id: str,
    reference: str,
    kind: str,
    neighbour_ticket_ids: list[str],
    airline: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """POC behaviour: treat any reference the customer gives as real by cloning the record behind the nearest
    historical case (falling back to any record of the right kind) under that reference. Customer PII is dropped."""
    template: m.OrderRecord | None = None
    for tid in neighbour_ticket_ids:
        order_id = session.execute(
            select(m.Ticket.order_id).where(m.Ticket.workspace_id == workspace_id, m.Ticket.ticket_id == tid)
        ).scalar_one_or_none()
        if order_id:
            template = (
                session.execute(
                    select(m.OrderRecord).where(
                        m.OrderRecord.workspace_id == workspace_id, m.OrderRecord.order_id == order_id
                    )
                )
                .scalars()
                .first()
            )
            if template:
                break
    if template is None:
        template = (
            session.execute(
                select(m.OrderRecord)
                .where(m.OrderRecord.workspace_id == workspace_id, m.OrderRecord.kind == kind)
                .order_by(m.OrderRecord.order_id)
            )
            .scalars()
            .first()
        )
    if template is None:
        return None
    data = json.loads(json.dumps(template.data, default=str))
    data["order_id"] = reference
    if kind == "booking":
        data["pnr"] = reference
    cust = data.get("customer") or {}
    data["customer"] = {k: v for k, v in cust.items() if k in ("customer_id", "loyalty_member", "loyalty_tier")}
    if kind == "booking" and data.get("flights"):
        # the template carries its own scenario; align it with what the customer actually described
        air = {k: v for k, v in (airline or {}).items() if v is not None}
        f = data["flights"][0]
        if air.get("is_cancelled_by_airline") is True:
            f.update(status="cancelled", is_cancelled_by_airline=True)
        elif (air.get("delay_minutes") or 0) > 0 or air.get("is_cancelled_by_airline") is False:
            f.update(
                status="delayed" if (air.get("delay_minutes") or 0) > 0 else "scheduled",
                is_cancelled_by_airline=False,
                schedule_change_minutes=0,
                is_significant_change=False,
                customer_accepted_alternative=bool(air.get("customer_accepted_alternative")),
            )
            data["customer_cancelled"] = bool(air.get("customer_cancelled"))
            data["status"] = "active"
        if "delay_minutes" in air:
            f["delay_minutes"] = air["delay_minutes"]
            f["arrival_delay_minutes"] = air["delay_minutes"]
        if "cause" in air:
            f["cause"] = air["cause"]
    data["stand_in"] = True
    data["stand_in_template"] = template.order_id
    session.add(m.OrderRecord(workspace_id=workspace_id, order_id=reference, kind=kind, data=data, is_synthetic=True))
    session.flush()
    return data


# ---------------------------------------------------------------- extract facts
YNU = {"type": "string", "enum": ["yes", "no", "unknown"]}
INT_U = {"type": "integer", "description": "-1 when unknown"}
NUM_U = {"type": "number", "description": "-1 when unknown"}
EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "reference": {"type": "string", "description": "order number or confirmation code mentioned, else empty"},
        "item_hint": {"type": "string", "description": "product the issue is about, else empty"},
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
            "properties": {
                "delay_minutes": INT_U,
                "cause": {"type": "string", "enum": ["controllable", "uncontrollable", "unknown"]},
                "is_cancelled_by_airline": YNU,
                "customer_accepted_alternative": YNU,
                "customer_cancelled": YNU,
                "bag_minutes_to_carousel": INT_U,
                "bag_report_filed": YNU,
                "bag_report_filed_within_hours": INT_U,
                "bag_delay_days": INT_U,
                "bag_damage_type": {"type": "string"},
                "interim_expenses": NUM_U,
                "overnight_required": YNU,
            },
            "required": [
                "delay_minutes",
                "cause",
                "is_cancelled_by_airline",
                "customer_accepted_alternative",
                "customer_cancelled",
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
    },
    "required": ["reference", "item_hint", "claim", "airline"],
    "additionalProperties": False,
}
EXTRACT_SYSTEM = f"""You extract claim facts from a customer conversation for Umpyr's rule engine. Only state what the customer
actually said or clearly implied; use "unknown"/-1/"" otherwise. Never guess. Text may contain [NAME]/[EMAIL] masks.
- claim.reason is what the customer asks about, one of {", ".join(CLAIM_REASONS)}. A request to cancel a backordered
  item is cancel_order. bag_guarantee = the bag was late to the carousel (20-minute guarantee); bag_delayed = delivered
  hours/days later; flight_delayed covers any request arising from a late flight; refund_request = the customer chose
  to cancel.
- has_photos = yes only if the customer says photos are attached/sent. has_receipt = yes if they have an order
  confirmation, receipt or order number.
- If the customer answered a question from the assistant (e.g. "yes, the box is unopened"), record that fact.
- airline fields only when the conversation is about a flight or bag; cause = controllable (crew, maintenance, IT)
  or uncontrollable (weather, air traffic control) only if the customer states the cause."""


def _clean(value):
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if value == "yes":
        return True
    if value == "no":
        return False
    if value in ("unknown", ""):
        return None
    if isinstance(value, int | float) and not isinstance(value, bool) and value == -1:
        return None
    return value


def extract_facts(
    llm: LLMProvider, workspace_id: str, history: list[dict[str, str]], record_summary: str | None, required: list[str]
) -> dict[str, Any]:
    convo = "\n\n".join(f"[{h['role'].upper()}]\n{h['text']}" for h in history)
    user = (
        f"Merchant record on file: {record_summary or 'none found yet'}\n"
        f"Facts the procedure needs: {required}\n\nConversation so far:\n{convo}"
    )
    res = (
        llm_json(
            llm,
            json_request(
                step="resolve.extract",
                system=EXTRACT_SYSTEM,
                user=user,
                schema=EXTRACT_SCHEMA,
                max_tokens=900,
                workspace_id=workspace_id,
            ),
        )
        or {}
    )
    return _clean(res)


# ---------------------------------------------------------------- draft reply
REPLY_SCHEMA = {
    "type": "object",
    "properties": {"reply": {"type": "string"}},
    "required": ["reply"],
    "additionalProperties": False,
}
REPLY_SYSTEM = """You write the customer-facing reply for Umpyr, a dispute-resolution engine. The DECISION HAS ALREADY BEEN MADE
by a deterministic rule engine; you only communicate it. Hard rules:
- Promise exactly what `ruling` says: same remedy, same amount, nothing more. Never offer alternatives, goodwill,
  exceptions, discounts or timelines that are not in `ruling` or `authorization`.
- If status is needs_fact: ask ONLY for the listed missing facts, in plain words, one short question each.
- If `ruling.record_lookup.reference_given` is set and `found` is false: the customer DID give a reference and it did
  not match any record. Say so, quote it back, and ask them to double-check it. Never say they have not provided one.
- Speak to the customer as "you". Never write "the customer", "the message we received" or other third-person framing.
- Do not recite route, dates, flight numbers or product names from `known_record`; the customer knows their own trip
  or order. Refer to it as "your booking" / "your order" plus the reference.
- If status is pending_approval: say the request has been sent for review and what the policy provides.
- If status is escalated: say a specialist will review and follow up; do not speculate on the outcome. If
  `ruling.record_lookup.found` is false, say the reference still does not match and that a colleague will locate the
  record from their name and travel or order date. Never claim a record is "on file" when found is false.
- If decision is deny: explain the policy reason in plain language (use `policy_reason`), without apologising excessively.
- Warm, concise, professional; 2-6 sentences; plain text; no markdown; sign as the merchant's support team.
- Never mention internal rule ids, Umpyr, or that a system decided."""


def draft_reply(llm: LLMProvider, workspace_id: str, payload: dict[str, Any]) -> str:
    res = (
        llm_json(
            llm,
            json_request(
                step="resolve.draft",
                system=REPLY_SYSTEM,
                user=json.dumps(payload, default=str, indent=1),
                schema=REPLY_SCHEMA,
                max_tokens=600,
                workspace_id=workspace_id,
            ),
        )
        or {}
    )
    return res.get("reply") or payload.get("fallback_reply") or ""


# ---------------------------------------------------------------- supervisor check
SUPERVISE_SCHEMA = {
    "type": "object",
    "properties": {"approved": {"type": "boolean"}, "issues": {"type": "array", "items": {"type": "string"}}},
    "required": ["approved", "issues"],
    "additionalProperties": False,
}
SUPERVISE_SYSTEM = """You are the supervisor check for Umpyr. Compare a drafted customer reply against the ruling it must
communicate. Reject (approved=false) if the reply: promises a remedy, amount, timeline or exception not in the ruling;
contradicts the decision; asks for facts other than the listed missing facts; or reveals internal ids. Minor wording
differences are fine. For status escalated, saying a specialist or colleague will review and follow up is REQUIRED,
not an issue. For needs_fact with an unmatched reference, saying the reference did not match and asking the customer
to re-check it is required. List at most three concrete issues, each under 20 words."""


def supervise(llm: LLMProvider, workspace_id: str, reply: str, ruling_payload: dict[str, Any]) -> dict[str, Any]:
    user = f"Ruling:\n{json.dumps(ruling_payload, default=str, indent=1)}\n\nDrafted reply:\n{reply}"
    res = llm_json(
        llm,
        json_request(
            step="resolve.supervise",
            system=SUPERVISE_SYSTEM,
            user=user,
            schema=SUPERVISE_SCHEMA,
            max_tokens=700,
            workspace_id=workspace_id,
        ),
    ) or {"approved": False, "issues": ["supervisor call failed"]}
    return res


def categories_for(session: Session, workspace_id: str) -> list[m.Category]:
    return list(
        session.execute(
            select(m.Category).where(m.Category.workspace_id == workspace_id, m.Category.status == "approved")
        ).scalars()
    )


def router_threshold() -> float:
    return THRESHOLD
