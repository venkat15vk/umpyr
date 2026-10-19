"""Resolve runtime: a code-driven state machine over the tools.

One customer message -> one turn:
  classify -> find_neighbours -> get_procedure -> lookup_record -> extract_facts -> rule -> authorize -> draft_reply
  -> supervise -> audit record. State (facts, category, record) persists on the session across turns so the engine
  can ask for a missing fact and continue.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session
from umpyr_rules import Actor, PDPPolicy, Resource, RulePack, audit_hash, authorize, evaluate
from umpyr_rules.pdp import Action, Mode

from ..db import models as m
from ..embeddings import EmbeddingProvider
from ..llm import LLMProvider
from ..sandbox.synth.core import NOW as SANDBOX_NOW
from . import tools
from .facts import build_facts, find_reference

log = logging.getLogger(__name__)

PROTOCOL = "umpyr-resolve/0.1"
REMEDY_ACTION = {
    "refund": Action.refund,
    "partial_refund": Action.partial_refund,
    "replacement": Action.replacement,
    "reship": Action.reship,
    "repair": Action.return_label,
    "return_label": Action.return_label,
    "store_credit": Action.store_credit,
    "travel_credit": Action.travel_credit,
    "rebook": Action.rebook,
    "points": Action.points,
    "voucher": Action.voucher,
    "expense_reimbursement": Action.expense_reimbursement,
    "bag_delivery": Action.bag_delivery,
    "cancel_order": Action.cancel_order,
}


def _as_of(ws: m.Workspace) -> datetime:
    s = (ws.settings or {}).get("as_of")
    if s:
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            pass
    return SANDBOX_NOW if ws.kind == "sandbox" else datetime.now(UTC).replace(tzinfo=None)


def _active_pack(session: Session, workspace_id: str) -> RulePack | None:
    row = session.query(m.RulePack).filter_by(workspace_id=workspace_id, status="active").first()
    return RulePack.model_validate(row.pack) if row else None


def _record_summary(record: dict | None, kind: str) -> str | None:
    if not record:
        return None
    if kind == "booking":
        f = (record.get("flights") or [{}])[0]
        return (
            f"booking {record.get('pnr')} fare={record.get('fare_class')} refundable={record.get('is_refundable_fare')} "
            f"paid={record.get('total_paid')} purchased={record.get('purchased_at')} flight {f.get('flight_number')} "
            f"{f.get('origin')}-{f.get('destination')} dep={f.get('scheduled_departure')} bags={len(record.get('bags') or [])} "
            f"seat_fee={record.get('ancillary_seat_fee')}"
        )
    items = ", ".join(f"{i.get('name')} (${i.get('line_total')})" for i in record.get("items") or [])
    ship = (record.get("shipments") or [{}])[0]
    return (
        f"order {record.get('order_id')} placed {record.get('placed_at')} status={record.get('status')} items: {items}; "
        f"shipped={ship.get('shipped_at')} delivered={ship.get('delivered_at')} payment={record.get('payment_method')} "
        f"rewards_discount={record.get('rewards_discount')}"
    )


def _merge_facts(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in old.items()}
    for sect, vals in new.items():
        if isinstance(vals, dict):
            cur = out.setdefault(sect, {})
            for k, v in vals.items():
                if v is not None:
                    cur[k] = v
        elif vals is not None:
            out[sect] = vals
    return out


def _jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=str))


def _step_table(
    procedure: m.Procedure | None, facts: dict[str, Any], ruling, pack: RulePack | None
) -> list[dict[str, Any]]:
    from umpyr_rules.jsonlogic import get_var

    checks = {c.rule_id: c for c in (ruling.checks if ruling else [])}
    fired_by_clause: dict[str, list[str]] = {}
    for c in checks.values():
        if c.fired:
            fired_by_clause.setdefault(c.clause_id, []).append(c.rule_id)
    rows = []
    steps = procedure.steps if procedure else []
    for s in steps:
        fields = {p: get_var(facts, p) for p in s.get("required_facts", [])}
        missing = [p for p, v in fields.items() if v is None]
        clause_ids = s.get("clause_ids", [])
        if s["kind"] == "closeout":
            status, detail = "placeholder", "disposition / recovery step (designed, not implemented)"
        elif s["kind"] in ("check", "decide"):
            fired = [rid for cid in clause_ids for rid in fired_by_clause.get(cid, [])]
            if ruling is None:
                status, detail = "pending", "no ruling yet"
            elif fired:
                status, detail = "fired", "rules fired: " + ", ".join(fired)
            elif str(ruling.decision) == "needs_fact":
                status, detail = "needs_fact", "waiting on: " + ", ".join(ruling.missing_facts)
            else:
                status, detail = "evaluated", "no governing rule fired for this step"
        elif s["kind"] in ("gather", "verify"):
            status, detail = ("needs_fact", "missing: " + ", ".join(missing)) if missing else ("ok", "facts on file")
        elif s["kind"] == "act":
            status, detail = (
                ("ready", "subject to authorization")
                if ruling and str(ruling.decision) in ("approve", "partial")
                else ("n/a", "no remedy to execute")
            )
        else:
            status, detail = "ok", ""
        clause_text = {}
        if pack:
            for cid in clause_ids:
                cl = pack.clause(cid)
                if cl:
                    clause_text[cid] = cl.text[:200]
        rows.append(
            {
                "step_id": s["step_id"],
                "name": s["name"],
                "kind": s["kind"],
                "tool": s.get("tool"),
                "clause_ids": clause_ids,
                "clauses": clause_text,
                "fields": _jsonable(fields),
                "status": status,
                "detail": detail,
            }
        )
    return rows


def run_turn(
    session: Session, llm: LLMProvider, embedder: EmbeddingProvider, chat: m.ChatSession, text: str
) -> dict[str, Any]:
    ws = session.get(m.Workspace, chat.workspace_id)
    kind = "booking" if ws.id == "alaska" or (ws.settings or {}).get("record_kind") == "booking" else "order"
    from ..ingest.pii import mask_text

    text = mask_text(text)  # PII masked before storage and before any model call
    state: dict[str, Any] = dict(chat.state or {})
    history: list[dict[str, str]] = list(state.get("history", []))
    history.append({"role": "customer", "text": text})
    turn = int(state.get("turn", 0)) + 1
    pack = _active_pack(session, ws.id)
    as_of = _as_of(ws)
    steps_log: list[dict[str, Any]] = []

    # 1. classify (router, LLM fallback)
    full_text = " ".join(h["text"] for h in history if h["role"] == "customer")
    key, conf, probs, vec = tools.classify_router(session, embedder, ws.id, full_text)
    classification: dict[str, Any] = {
        "category_key": key,
        "confidence": round(conf, 3),
        "source": "router",
        "probabilities": {k: round(v, 3) for k, v in sorted(probs.items(), key=lambda kv: -kv[1])[:4]},
    }
    cats = tools.categories_for(session, ws.id)
    if key is None or conf < tools.router_threshold():
        res = tools.classify_llm(llm, ws.id, full_text, cats)
        classification.update(
            {
                "category_key": res.get("category_key"),
                "confidence": round(float(res.get("confidence", 0)), 3),
                "source": "llm_fallback",
                "multi_issue": res.get("multi_issue"),
                "secondary_category_key": res.get("secondary_category_key") or None,
                "rationale": res.get("rationale"),
            }
        )
        key = classification["category_key"]
    cat = next((c for c in cats if c.key == key), None)
    classification["category_name"] = cat.name if cat else key
    steps_log.append(
        {
            "tool": "classify",
            "ok": True,
            "detail": f"{key} @ {classification['confidence']} ({classification['source']})",
        }
    )

    # 2. neighbours
    neighbours = tools.find_neighbours(session, ws.id, vec)
    steps_log.append(
        {
            "tool": "find_neighbours",
            "ok": True,
            "detail": f"{len(neighbours)} neighbours, {sum(n['is_drift'] for n in neighbours)} with drift",
        }
    )

    # 3. procedure
    procedure = tools.get_procedure(session, ws.id, key)
    proc_ref = f"{procedure.category_key}@{procedure.version}" if procedure else None
    steps_log.append(
        {"tool": "get_procedure", "ok": procedure is not None, "detail": proc_ref or "no approved procedure"}
    )

    # 4. lookup record (reference from this message, earlier state, or extraction)
    reference = state.get("reference") or find_reference(text, kind)
    record = tools.lookup_record(session, ws.id, reference) if reference else None

    # 5. extract facts
    required = list(procedure.required_facts) if procedure else []
    extracted = tools.extract_facts(llm, ws.id, history, _record_summary(record, kind), required)
    if not record and extracted.get("reference"):
        reference = extracted["reference"].upper()
        record = tools.lookup_record(session, ws.id, reference)
    steps_log.append(
        {
            "tool": "lookup_order",
            "ok": record is not None,
            "detail": f"{reference or 'no reference'} -> {'found' if record else 'not found'}",
        }
    )
    prior_claim = (state.get("facts") or {}).get("claim", {})
    claim = {**prior_claim, **{k: v for k, v in (extracted.get("claim") or {}).items() if v is not None}}
    airline = {
        **(state.get("airline") or {}),
        **{k: v for k, v in (extracted.get("airline") or {}).items() if v is not None},
    }
    facts = build_facts(
        claim,
        airline,
        record,
        as_of,
        kind,
        item_hint=((state.get("facts") or {}).get("item") or {}).get("sku")
        or extracted.get("item_hint")
        or state.get("item_hint"),
    )
    facts = _merge_facts(state.get("facts") or {}, facts)
    steps_log.append(
        {
            "tool": "extract_facts",
            "ok": True,
            "detail": f"claim.reason={claim.get('reason')} ({len(claim)} claim facts)",
        }
    )

    # 6. rule
    ruling = evaluate(pack, facts, category=key) if pack else None
    if ruling is None:
        status = "escalated"
    else:
        status = {
            "approve": "resolved",
            "partial": "resolved",
            "deny": "denied",
            "needs_fact": "needs_fact",
            "needs_approval": "pending_approval",
            "escalate": "escalated",
        }[str(ruling.decision)]
        if ruling.conflicts:
            status = "escalated"
    if ruling is not None and not record and str(ruling.decision) == "needs_fact":
        # without the merchant record, the only useful question is the reference itself
        ruling.missing_facts = ["order reference" if kind == "order" else "confirmation code"]
        ruling.explanation = "The merchant record is needed before a ruling can be made."
    if ruling is not None and not record and str(ruling.decision) in ("approve", "partial", "deny", "escalate"):
        # never rule without the merchant record: ask for it first
        from umpyr_rules import Ruling as _R

        ruling = _R(
            decision="needs_fact",
            missing_facts=["order reference" if kind == "order" else "confirmation code"],
            checks=ruling.checks,
            rule_pack_version=ruling.rule_pack_version,
            explanation="The merchant record is needed before a ruling can be made.",
        )
        status = "needs_fact"
    steps_log.append(
        {
            "tool": "rule",
            "ok": ruling is not None,
            "detail": f"{ruling.decision if ruling else 'no rule pack'} {ruling.winning_rule_id or ''}".strip()
            if ruling
            else "no rule pack",
        }
    )

    # 7. authorize (PDP)
    policy = PDPPolicy(
        workspace=ws.id, mode=Mode(chat.mode), auto_approve_limit=Decimal(str(chat.auto_approve_limit or 100))
    )
    authorization = None
    if (
        ruling is not None
        and str(ruling.decision) in ("approve", "partial")
        and ruling.remedy
        and ruling.remedy != "none"
    ):
        action = REMEDY_ACTION.get(str(ruling.remedy), Action.escalate)
        max_amount = None
        if record:
            max_amount = Decimal(str(record.get("total") or record.get("total_paid") or 0)) or None
        resource = Resource(
            type=kind,
            id=reference or "unknown",
            amount=ruling.amount,
            max_amount=max_amount if (max_amount and ruling.amount and ruling.amount <= max_amount * 2) else None,
        )
        authorization = authorize(Actor(actor_id="umpyr-agent", role="agent"), action, resource, facts, ruling, policy)
        if authorization.decision == "needs_approval":
            status = "pending_approval"
        elif authorization.decision == "deny":
            status = "escalated"
    steps_log.append(
        {
            "tool": "authorize",
            "ok": True,
            "detail": f"{authorization.decision} ({'; '.join(authorization.reasons)})"
            if authorization
            else "no action to authorize",
        }
    )

    # 8. draft reply (+ 9. supervisor)
    clause_notes = []
    if ruling and pack:
        for cid in ruling.clause_ids[:3]:
            cl = pack.clause(cid)
            if cl:
                clause_notes.append({"clause_id": cid, "title": cl.title, "text": cl.text[:300]})
    ruling_payload = {
        "status": status,
        "decision": str(ruling.decision) if ruling else "escalate",
        "remedy": ruling.remedy if ruling else None,
        "amount": str(ruling.amount) if ruling and ruling.amount is not None else None,
        "policy_reason": (ruling.explanation if ruling else "no rule pack"),
        "missing_facts": _friendly_missing(ruling.missing_facts) if ruling else [],
        "authorization": authorization.decision if authorization else None,
        "clauses": clause_notes,
    }
    draft_payload = {
        "merchant": ws.name,
        "channel": chat.channel,
        "customer_message": text,
        "known_record": _record_summary(record, kind),
        "ruling": ruling_payload,
        "fallback_reply": _fallback_reply(ruling_payload),
    }
    reply = tools.draft_reply(llm, ws.id, draft_payload)
    supervisor = tools.supervise(llm, ws.id, reply, ruling_payload)
    if not supervisor.get("approved"):
        draft_payload["supervisor_feedback"] = supervisor.get("issues")
        reply2 = tools.draft_reply(llm, ws.id, draft_payload)
        supervisor2 = tools.supervise(llm, ws.id, reply2, ruling_payload)
        if supervisor2.get("approved"):
            reply, supervisor = reply2, {**supervisor2, "retried": True}
        else:
            reply = _fallback_reply(ruling_payload)
            supervisor = {**supervisor2, "retried": True, "fallback_used": True}
    steps_log.append({"tool": "draft_reply", "ok": True, "detail": f"{len(reply)} chars"})
    steps_log.append(
        {
            "tool": "supervisor",
            "ok": bool(supervisor.get("approved")),
            "detail": "; ".join(supervisor.get("issues") or []) or "reply matches ruling",
        }
    )

    # 10. audit
    audit = {
        "ticket_id": str(chat.id),
        "turn": turn,
        "workspace_id": ws.id,
        "procedure": proc_ref,
        "rule_pack_version": pack.version if pack else None,
        "category": key,
        "facts": _jsonable(facts),
        "checks": [c.model_dump(mode="json") for c in (ruling.checks if ruling else []) if c.applicable],
        "ruling": ruling.model_dump(mode="json", exclude={"checks"}) if ruling else None,
        "authorization": authorization.model_dump(mode="json") if authorization else None,
        "mode": chat.mode,
        "supervisor": supervisor,
        "pii_masked": True,
    }
    h = audit_hash(audit)
    rec = m.RulingRecord(
        workspace_id=ws.id,
        session_id=chat.id,
        ticket_id=str(chat.id),
        category_key=key,
        procedure_id=procedure.id if procedure else None,
        procedure_version=proc_ref,
        rule_pack_version=pack.version if pack else None,
        facts=audit["facts"],
        checks=audit["checks"],
        ruling=audit["ruling"] or {},
        authorization=audit["authorization"],
        supervisor=supervisor,
        mode=chat.mode,
        audit_hash=h,
    )
    session.add(rec)

    # agent-channel payload
    agent_message = {
        "protocol": PROTOCOL,
        "session_id": str(chat.id),
        "turn": turn,
        "status": status,
        "decision": ruling_payload["decision"],
        "remedy": ruling_payload["remedy"],
        "amount": ruling_payload["amount"],
        "currency": "USD",
        "fault": ruling.fault if ruling else None,
        "clause_ids": ruling.clause_ids if ruling else [],
        "explanation": ruling_payload["policy_reason"],
        "required_facts": ruling.missing_facts if ruling else [],
        "message": reply,
        "audit_hash": h,
    }

    ops = {
        "turn": turn,
        "status": status,
        "classification": classification,
        "neighbours": neighbours,
        "procedure": {
            "ref": proc_ref,
            "name": procedure.name if procedure else None,
            "steps": _step_table(procedure, facts, ruling, pack),
        },
        "record": {"reference": reference, "found": record is not None, "summary": _record_summary(record, kind)},
        "facts": _jsonable(facts),
        "ruling": {
            **(ruling.model_dump(mode="json", exclude={"checks"}) if ruling else {}),
            "status": status,
            "clause_notes": clause_notes,
        },
        "checks": [c.model_dump(mode="json") for c in (ruling.checks if ruling else []) if c.applicable],
        "authorization": authorization.model_dump(mode="json") if authorization else None,
        "supervisor": supervisor,
        "governance": {
            "pii_masked": True,
            "rule_pack_version": pack.version if pack else None,
            "procedure": proc_ref,
            "authority": f"{chat.mode} / auto-approve ≤ ${chat.auto_approve_limit}",
            "supervisor_check": "passed" if supervisor.get("approved") else "failed",
            "audit_hash": h,
            "is_synthetic": ws.is_synthetic,
        },
        "tools": steps_log,
        "agent_message": agent_message if chat.channel == "agent" else None,
    }

    history.append({"role": "assistant", "text": reply})
    chat.state = {
        **state,
        "history": history,
        "turn": turn,
        "facts": _jsonable(facts),
        "airline": airline,
        "reference": reference,
        "item_hint": extracted.get("item_hint") or state.get("item_hint"),
        "category_key": key,
        "last_status": status,
    }
    chat.status = (
        "open" if status in ("needs_fact",) else ("resolved" if status in ("resolved", "denied") else "pending")
    )
    session.add(chat)
    session.add(m.ChatMessage(session_id=chat.id, workspace_id=ws.id, role="customer", content=text, payload={}))
    session.add(m.ChatMessage(session_id=chat.id, workspace_id=ws.id, role="assistant", content=reply, payload=ops))
    session.commit()
    return {"reply": reply, "ops": ops}


FRIENDLY = {
    "claim.has_photos": "photos of the damage or defect",
    "claim.has_receipt": "proof of purchase (order number or receipt)",
    "claim.item_unopened": "whether the item is still unopened in its original packaging",
    "claim.item_used": "whether the item has been used",
    "claim.requested_amount": "the amount you are claiming (with receipts)",
    "claim.reported_within_days": "when you first noticed the problem",
    "bag.report_filed": "whether a baggage report (File ID) was filed at the airport",
    "bag.report_filed_within_hours": "when the baggage report was filed",
    "bag.minutes_to_carousel": "how long after arrival the bag reached the carousel",
    "bag.guarantee_claim_filed_within_hours": "whether you spoke to the baggage office within two hours of arrival",
    "bag.delay_days": "how long the bag was delayed",
    "bag.interim_expenses": "the essentials you had to buy while the bag was delayed (amount)",
    "bag.damage_type": "what kind of damage the bag has",
    "flight.cause": "the reason the airline gave for the delay or cancellation",
    "flight.customer_accepted_alternative": "whether you took the rebooked flight or chose not to travel",
    "flight.delay_minutes": "how long the delay was",
    "flight.overnight_required": "whether the disruption forced an overnight stay",
    "flight.schedule_change_minutes": "how much the schedule moved",
    "booking.customer_cancelled": "whether you cancelled the booking yourself",
}


def _friendly_missing(paths: list[str]) -> list[str]:
    return [FRIENDLY.get(p, p.replace("_", " ").replace(".", ": ")) for p in paths]


def _fallback_reply(rp: dict[str, Any]) -> str:
    st = rp["status"]
    if st == "needs_fact":
        asks = "; ".join(rp["missing_facts"]) or "a few more details"
        return (
            f"Thanks for reaching out. To look into this I need: {asks}. Once I have that I can confirm what we can do."
        )
    if st == "pending_approval":
        return (
            f"Thanks for the details. Policy provides for a {rp['remedy']} here"
            + (f" of ${rp['amount']}" if rp["amount"] else "")
            + ", and the request has been sent to a supervisor for authorization. We will follow up shortly."
        )
    if st == "escalated":
        return "Thanks for the details. This one needs a specialist to review; we have passed it on and will follow up with you directly."
    if st == "denied":
        return f"Thanks for the details. Unfortunately we are not able to approve this request: {rp['policy_reason']}"
    return (
        f"Good news: we can offer a {rp['remedy']}"
        + (f" of ${rp['amount']}" if rp["amount"] else "")
        + f". {rp['policy_reason']}"
    )
