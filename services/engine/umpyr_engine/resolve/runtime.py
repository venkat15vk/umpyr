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

from ..check import get_check
from ..compile.provenance import citation as _citation
from ..config import get_settings
from ..db import models as m
from ..embeddings import EmbeddingProvider
from ..llm import LLMProvider
from ..llm.meter import CostMeter, metered
from ..sandbox.synth.core import NOW as SANDBOX_NOW
from . import customer as cx
from . import tools
from .facts import build_facts, find_reference
from .ledger import CaseLedger
from .planner import history_backs_ruling, plan_case
from .status import status_event

MAX_LOOKUP_MISSES = 3  # after this many unmatched references, hand the case to a person

log = logging.getLogger(__name__)

PROTOCOL = "umpyr-resolve/0.2"
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


def _escalation(
    status, ruling, record, pack, authorization, missed, plan=None, planner_gate_hit=False
) -> dict[str, Any] | None:
    """Typed escalation (design v2 §7). Only escalated and queued turns carry one."""
    novel = bool(plan and plan.get("novel"))
    if status == "pending_approval" and planner_gate_hit:
        return {
            "type": "planner_disagreement",
            "reason": (
                f"confident precedent plans {plan['historical']['steps']} but this ruling is "
                f"{ruling.decision}/{ruling.remedy}; live planner proposed {plan['live']['steps']}"
            ),
            "novel_case": novel,
        }
    if status == "escalated" and novel and not record:
        return {"type": "data_gap", "reason": "no close precedent and no merchant record", "novel_case": True}
    if status == "pending_approval":
        return {
            "type": "governance_gate",
            "reason": "; ".join(authorization.reasons) if authorization else "ruling needs approval",
        }
    if status != "escalated":
        return None
    if pack is None:
        return {"type": "policy_gap", "reason": "no active rule pack for this workspace"}
    if authorization is not None and authorization.decision == "deny":
        return {"type": "governance_gate", "reason": "; ".join(authorization.reasons) or "PDP denied the action"}
    if ruling is not None and ruling.conflicts:
        return {"type": "policy_gap", "reason": "conflicting rules: " + "; ".join(ruling.conflicts)}
    if not record:
        return {"type": "data_gap", "reason": f"no merchant record matched after {len(missed)} reference(s)"}
    if ruling is not None and "No compiled rule matched" in (ruling.explanation or ""):
        return {"type": "policy_gap", "reason": "no compiled rule matched these facts"}
    return {"type": "policy_gap", "reason": (ruling.explanation if ruling else None) or "unresolved"}


def run_turn(
    session: Session, llm: LLMProvider, embedder: EmbeddingProvider, chat: m.ChatSession, text: str
) -> dict[str, Any]:
    """One customer message -> one turn, inside the case ledger and a cost meter (design v2 §3, §15, §1)."""
    ledger = CaseLedger.open(session, chat)
    ledger.begin_turn()
    with metered(ledger.case.id, chat.id) as meter:
        return _run_turn(session, llm, embedder, chat, text, ledger, meter)


def _run_turn(
    session: Session,
    llm: LLMProvider,
    embedder: EmbeddingProvider,
    chat: m.ChatSession,
    text: str,
    ledger: CaseLedger,
    meter: CostMeter,
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
    case = ledger.case
    cost_before = Decimal(case.cost_usd or 0)
    status_events: list[dict[str, Any]] = []

    def emit(action: str, **ctx: Any) -> None:
        ev = status_event(action, kind=kind, **ctx)
        if ev["message"]:
            status_events.append(ev)

    def stage(actor: str, action: str, basis: dict[str, Any] | None = None, result: dict[str, Any] | None = None):
        used = meter.take()
        b = dict(basis or {})
        if used["calls"]:
            b["llm"] = {"calls": used["calls"], "cost_usd": str(used["cost_usd"]), "steps": used["steps"]}
        ledger.append(actor, action, basis=b, result=result, cost_usd=used["cost_usd"], llm_calls=used["calls"])

    ledger.append(
        "customer", "message.received", basis={"channel": chat.channel}, result={"text": text, "chars": len(text)}
    )
    emit("message.received")
    check_signal = get_check().on_ingest(ws.id, str(case.id), text, state.get("facts"))
    ledger.append("check", "check.ingest", basis={"provider": check_signal.provider}, result=check_signal.as_dict())

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
    stage(
        "umpyr",
        "classification",
        basis={"source": classification["source"], "router_threshold": tools.router_threshold()},
        result={"category_key": key, "confidence": classification["confidence"]},
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
    stage(
        "umpyr",
        "precedent.retrieved",
        basis={"k": len(neighbours)},
        result={
            "neighbours": [
                {"ticket_id": n["ticket_id"], "similarity": n["similarity"], "is_drift": n["is_drift"]}
                for n in neighbours
            ]
        },
    )
    emit("precedent.retrieved")

    # 3. procedure
    procedure = tools.get_procedure(session, ws.id, key)
    proc_ref = f"{procedure.category_key}@{procedure.version}" if procedure else None
    steps_log.append(
        {"tool": "get_procedure", "ok": procedure is not None, "detail": proc_ref or "no approved procedure"}
    )
    stage(
        "umpyr",
        "plan.selected",
        basis={"category_key": key},
        result={
            "procedure": proc_ref,
            "steps": [s.get("step_id") for s in (procedure.steps or [])] if procedure else [],
        },
    )

    # 4. lookup record (reference from this message, earlier state, or extraction)
    reference = state.get("reference") or find_reference(text, kind)
    record = tools.lookup_record(session, ws.id, reference) if reference else None
    missed: list[str] = list(state.get("missed_references") or [])

    # 5. extract facts
    required = list(procedure.required_facts) if procedure else []
    known = (
        {
            "claim": (state.get("facts") or {}).get("claim") or {},
            "airline": state.get("airline") or {},
            "intents": state.get("intents") or [],
            "party": state.get("party") or {},
            "reference": state.get("reference"),
        }
        if turn > 1
        else None
    )
    extracted = tools.extract_facts(llm, ws.id, history, _record_summary(record, kind), required, known=known)
    if not record and extracted.get("reference"):
        reference = extracted["reference"].upper()
        record = tools.lookup_record(session, ws.id, reference)
    stand_in = False
    airline = {
        **(state.get("airline") or {}),
        **{k: v for k, v in (extracted.get("airline") or {}).items() if v is not None},
    }
    if reference and not record and get_settings().assume_reference_exists:
        record = tools.stand_in_record(
            session,
            ws.id,
            reference,
            kind,
            [n["ticket_id"] for n in neighbours],
            airline if kind == "booking" else None,
            claim={
                **((state.get("facts") or {}).get("claim") or {}),
                **{k: v for k, v in (extracted.get("claim") or {}).items() if v is not None},
            },
        )
        stand_in = record is not None
    repeated_miss = bool(reference and not record and reference in missed)  # customer re-confirmed a bad code
    if reference and not record and reference not in missed:
        missed.append(reference)
    lookup_miss = reference if (reference and not record) else None
    steps_log.append(
        {
            "tool": "lookup_order",
            "ok": record is not None,
            "detail": f"{reference or 'no reference'} -> "
            + ("stand-in record created (POC: references are assumed to exist)" if stand_in else "")
            + ("found" if record and not stand_in else "")
            + ("not found" if not record else "")
            + (f" ({len(missed)} unmatched so far)" if lookup_miss else ""),
        }
    )
    stage(
        "umpyr",
        "record.lookup",
        basis={"reference": reference, "assume_reference_exists": get_settings().assume_reference_exists},
        result={"found": record is not None, "stand_in": stand_in, "unmatched_references": missed},
    )
    emit("record.lookup" if record else "record.missing")
    prior_claim = (state.get("facts") or {}).get("claim", {})
    new_claim = {k: v for k, v in (extracted.get("claim") or {}).items() if v is not None}
    if known:  # delta mode: enum fields have no "unknown" sentinel, so "other" means "unchanged"
        if new_claim.get("reason") == "other" and prior_claim.get("reason"):
            new_claim.pop("reason")
        if new_claim.get("requested_remedy") in (None, "unknown") and prior_claim.get("requested_remedy"):
            new_claim.pop("requested_remedy", None)
    claim = {**prior_claim, **new_claim}
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
    intents = list(dict.fromkeys([*(state.get("intents") or []), *(extracted.get("intents") or [])]))
    party = {
        **(state.get("party") or {}),
        **{k: v for k, v in (extracted.get("party") or {}).items() if v not in (None, [])},
    }
    facts.setdefault("claim", {})
    facts["claim"]["intents"] = intents
    if extracted.get("timeline_ask") is not None:
        facts["claim"]["timeline_ask"] = extracted["timeline_ask"]
    if extracted.get("reference_kind") not in (None, "none"):
        facts["claim"]["reference_kind"] = extracted["reference_kind"]
    if party:
        facts["party"] = party
    facts = _merge_facts(state.get("facts") or {}, facts)
    steps_log.append(
        {
            "tool": "extract_facts",
            "ok": True,
            "detail": f"claim.reason={claim.get('reason')} ({len(claim)} claim facts)",
        }
    )
    stage(
        "umpyr",
        "extraction",
        basis={"schema": "v2", "mode": "delta" if known else "full", "required_facts": required},
        result={
            "claim_keys": sorted(k for k, v in claim.items() if v is not None),
            "reference": extracted.get("reference"),
            "intents": intents,
            "party": party or None,
            "timeline_ask": facts["claim"].get("timeline_ask"),
            "fact_sections": sorted(facts.keys()),
        },
    )

    # 5a. refund-option choice (options were offered on an earlier turn)
    prior_options = list(state.get("options") or [])
    chosen = cx.detect_choice(prior_options, extracted, text) or state.get("chosen_remedy")

    # 5b. plan: historical (neighbours' consensus) vs live (model), compared by step type and order
    settings = get_settings()
    plan = plan_case(
        session,
        llm,
        embedder,
        ws.id,
        category=key,
        intents=intents,
        claim=facts["claim"],
        facts=facts,
        party=party or None,
        procedure_steps=[s.get("name") for s in (procedure.steps or [])] if procedure else [],
        record_summary=_record_summary(record, kind),
        k=settings.plan_neighbours_k,
        novelty_min_similarity=settings.novelty_min_similarity,
        agreement_min=settings.plan_agreement_min,
    )
    ledger.append(
        "umpyr",
        "plan.historical",
        basis={"k": plan["historical"]["k"], "case_text": plan["case_text"]},
        result={
            "steps": plan["historical"]["steps"],
            "support": plan["historical"]["support"],
            "agreement": plan["historical"]["agreement"],
            "mean_similarity": plan["historical"]["mean_similarity"],
            "neighbours": [
                {"ticket_id": n["ticket_id"], "similarity": n["similarity"]} for n in plan["historical"]["neighbours"]
            ],
            "novel": plan["novel"],
        },
    )
    stage(
        "umpyr",
        "plan.live",
        basis={"vocabulary": "staff_actions"},
        result={"raw": plan["live"]["raw"], "steps": plan["live"]["steps"], "rationale": plan["live"]["rationale"]},
    )
    ledger.append(
        "umpyr",
        "plan.compared",
        basis={"rule": "step type and order, not count; history wins unless it diverges"},
        result={
            **plan["comparison"],
            "chosen": plan["chosen"],
            "steps": plan["steps"],
            "disagreement": plan["disagreement"],
        },
    )
    steps_log.append(
        {
            "tool": "plan",
            "ok": not plan["disagreement"],
            "detail": f"{plan['comparison']['kind']}: history {plan['historical']['steps']} vs live {plan['live']['steps']}"
            + (" (novel case)" if plan["novel"] else ""),
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
    if ruling is not None and not record:
        # never rule without the merchant record: ask for the reference, or hand off after repeated misses
        from umpyr_rules import Ruling as _R

        ref_name = "order number" if kind == "order" else "confirmation code"
        if repeated_miss or len(missed) >= MAX_LOOKUP_MISSES:
            ruling = _R(
                decision="escalate",
                checks=ruling.checks,
                rule_pack_version=ruling.rule_pack_version,
                explanation=(
                    (
                        f"The customer confirmed {lookup_miss} twice and it matches no record; "
                        if repeated_miss
                        else f"None of the references given ({', '.join(missed)}) match a record; "
                    )
                    + "a person will locate the record from the customer's name and travel or order date."
                ),
            )
            status = "escalated"
        else:
            ask = (
                f"{ref_name} ({lookup_miss} did not match any record; please double-check it against the itinerary"
                " or receipt)"
                if lookup_miss
                else ref_name
            )
            ruling = _R(
                decision="needs_fact",
                missing_facts=[ask],
                checks=ruling.checks,
                rule_pack_version=ruling.rule_pack_version,
                explanation="The merchant record is needed before a ruling can be made.",
            )
            status = "needs_fact"
    # never ask the same question a third time: a fact asked twice already is a data gap for a person (D50)
    asked: dict[str, int] = dict(state.get("asked_facts") or {})
    forced_escalation = None
    if status == "needs_fact" and ruling is not None and ruling.missing_facts:
        repeated = [f for f in ruling.missing_facts if asked.get(f, 0) >= 2]
        if repeated:
            from umpyr_rules import Ruling as _R

            ruling = _R(
                decision="escalate",
                checks=ruling.checks,
                rule_pack_version=ruling.rule_pack_version,
                explanation=(
                    f"The customer was asked twice for {', '.join(_friendly_missing(repeated))} and answered, but the "
                    "answers could not be captured; a person will take over rather than ask again."
                ),
            )
            status = "escalated"
            forced_escalation = {"type": "data_gap", "reason": "asked twice, answer not captured", "facts": repeated}
        else:
            for f in ruling.missing_facts:
                asked[f] = asked.get(f, 0) + 1
    options = cx.refund_options(ruling, pack) if status in ("resolved", "pending_approval") else []
    if ruling is not None and chosen and options:
        ruling = cx.apply_choice(ruling, options, chosen, facts)
    if options and not prior_options:
        ledger.append(
            "umpyr",
            "options_offered",
            basis={"rule": "policy lists the options; cash first when regulation guarantees it; never defaulted"},
            result={
                "options": [
                    {k: o[k] for k in ("remedy", "amount", "rule_id", "guaranteed_by_regulation")} for o in options
                ]
            },
        )
        emit("options.review")
    if chosen and chosen != state.get("chosen_remedy") and options:
        ledger.append(
            "customer", "choice", basis={"offered": [o["remedy"] for o in options]}, result={"chosen": chosen}
        )
    steps_log.append(
        {
            "tool": "rule",
            "ok": ruling is not None,
            "detail": f"{ruling.decision if ruling else 'no rule pack'} {ruling.winning_rule_id or ''}".strip()
            if ruling
            else "no rule pack",
        }
    )
    # citations: resolved from rule provenance, never generated (design v2 §14)
    cited_ids = list(
        dict.fromkeys(
            (ruling.clause_ids if ruling else [])
            + [cid for s in (procedure.steps if procedure else []) for cid in (s.get("clause_ids") or [])]
        )
    )
    citations: dict[str, dict[str, Any]] = {}
    for cid in cited_ids:
        cl = pack.clause(cid) if pack else None
        if cl:
            citations[cid] = _citation(cl)
    ruling_citations = [citations[c] for c in (ruling.clause_ids if ruling else []) if c in citations]
    stage(
        "umpyr",
        "rule_check",
        basis={
            "rule_pack_version": pack.version if pack else None,
            "fired_rule_ids": ruling.fired_rule_ids if ruling else [],
            "citations": [
                {k: c.get(k) for k in ("clause_id", "document_id", "document_version", "section", "page", "text_hash")}
                for c in ruling_citations
            ],
        },
        result={
            "decision": str(ruling.decision) if ruling else None,
            "remedy": ruling.remedy if ruling else None,
            "amount": str(ruling.amount) if ruling and ruling.amount is not None else None,
            "winning_rule_id": ruling.winning_rule_id if ruling else None,
            "status": status,
            "missing_facts": ruling.missing_facts if ruling else [],
        },
    )
    emit("rule_check")

    # 7. authorize (PDP)
    policy = PDPPolicy(
        workspace=ws.id, mode=Mode(chat.mode), auto_approve_limit=Decimal(str(chat.auto_approve_limit or 100))
    )
    authorization = None
    action = None
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
    planner_gate_hit = False
    backed = history_backs_ruling(plan, str(ruling.decision) if ruling else None, ruling.remedy if ruling else None)
    plan["history_backs_ruling"] = backed
    if (
        plan["disagreement"]
        and backed is False  # confident precedent contradicts the ruling's remedy; the live planner alone never gates
        and settings.planner_gate
        and authorization is not None
        and authorization.decision == "allow"
        and status == "resolved"
    ):
        status = "pending_approval"  # history and the live planner disagree on the remedy: a person confirms
        planner_gate_hit = True
    steps_log.append(
        {
            "tool": "authorize",
            "ok": True,
            "detail": f"{authorization.decision} ({'; '.join(authorization.reasons)})"
            if authorization
            else "no action to authorize",
        }
    )
    if authorization is not None:
        verdict = get_check().pre_execution(ws.id, str(case.id), str(action), ruling.amount, facts)
        ledger.append(
            "check",
            "check.pre_execution",
            basis={"provider": verdict.provider, "action": str(action)},
            result=verdict.as_dict(),
        )
    stage(
        "umpyr",
        "gate",
        basis={
            "mode": chat.mode,
            "auto_approve_limit": str(chat.auto_approve_limit),
            "action": str(action) if action else None,
            "planner_gate": planner_gate_hit,
        },
        result={
            "decision": authorization.decision if authorization else "no_action",
            "reasons": authorization.reasons if authorization else [],
            "status": status,
        },
    )
    escalation = forced_escalation or _escalation(
        status, ruling, record, pack, authorization, missed, plan, planner_gate_hit
    )
    gap = (
        {
            "type": "data_gap",
            "reason": "record not found" if not record else "required facts missing",
            "asks": ruling.missing_facts if ruling else [],
        }
        if status == "needs_fact"
        else None
    )
    if escalation:
        ledger.append("umpyr", "escalation", basis={"type": escalation["type"]}, result=escalation)
        emit("escalation" if status == "escalated" else "gate.queued")
    elif status == "needs_fact":
        emit("needs_fact")
    elif authorization is not None and authorization.decision == "allow":
        emit("gate.allowed")

    # 7b. queued customer: pending message always, SLA when configured, honest escalation when overdue (v2 §11)
    sla_hours = (ws.settings or {}).get("approval_sla_hours")
    queue = None
    if status == "pending_approval":
        queue = cx.queue_state(state.get("queue"), sla_hours, cx.now_utc())
        if queue["overdue"]:
            status = "escalated"
            escalation = {
                "type": "governance_gate",
                "reason": f"approval SLA of {sla_hours} hours missed; a specialist now owns the case",
                "overdue": True,
            }
            ledger.append("umpyr", "queue.overdue", basis={"due_at": queue["due_at"]}, result={"sla_hours": sla_hours})
            emit("queue.overdue")

    # 7c. next-question prediction -> prefetch the refund timeline and answer it proactively (v2 §5, §13)
    predicted = cx.predict_next_questions(session, ws.id, key)
    proactive: list[dict[str, Any]] = []
    wants_timeline = bool(facts["claim"].get("timeline_ask")) or any(
        p["question"] == "timeline" and p["probability"] >= settings.proactive_min for p in predicted
    )
    if (
        wants_timeline
        and ruling is not None
        and str(ruling.decision) in ("approve", "partial")
        and ruling.remedy in ("refund", "partial_refund")
        and status in ("resolved", "pending_approval")
    ):
        tl = cx.timeline_answer(pack, (record or {}).get("payment_method"))
        if tl:
            proactive.append(tl)
    ledger.append(
        "umpyr",
        "prefetch",
        basis={"predicted_next_questions": predicted, "timeline_asked": bool(facts["claim"].get("timeline_ask"))},
        result={
            "proactive": [
                {
                    "question": p["question"],
                    "days": p.get("days"),
                    "clause_id": (p.get("citation") or {}).get("clause_id"),
                }
                for p in proactive
            ]
        },
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
        "citations": ruling_citations,
        "public_citations": cx.public_citations(ruling_citations),
        "escalation": escalation,
        "options": [
            {k: o[k] for k in ("remedy", "label", "amount", "guaranteed_by_regulation", "is_cash")} for o in options
        ],
        "chosen_remedy": chosen if options else None,
        "proactive": [{"question": p["question"], "answer": p["answer"]} for p in proactive],
        "queue": {"sla": cx.sla_sentence(sla_hours), "overdue": bool(queue and queue.get("overdue"))}
        if queue
        else None,
        "record_lookup": {
            "reference_given": reference,
            "found": record is not None,
            "unmatched_references": missed,
        },
    }
    draft_payload = {
        "merchant": ws.name,
        "channel": chat.channel,
        "customer_message": text,
        "known_record": _record_summary(record, kind),
        "ruling": ruling_payload,
        "customer_status": cx.status_lines(status_events, kind),
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
    stage(
        "umpyr",
        "notification.drafted",
        basis={
            "channel": chat.channel,
            "supervisor": {k: supervisor.get(k) for k in ("approved", "retried", "fallback_used")},
        },
        result={"chars": len(reply)},
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
    stage(
        "umpyr",
        "decision",
        basis={"audit_hash": h, "rule_pack_version": pack.version if pack else None, "procedure": proc_ref},
        result={
            "status": status,
            "decision": ruling_payload["decision"],
            "remedy": ruling_payload["remedy"],
            "amount": ruling_payload["amount"],
            "escalation": escalation,
        },
    )
    decision_hash = ledger.commit_decision(h)
    if status in ("resolved", "denied"):
        emit(f"decision.{status}")
    case.status = status
    case.due_at = (
        datetime.fromisoformat(queue["due_at"])
        if queue and queue.get("due_at") and status == "pending_approval"
        else None
    )
    case.category_key = key
    case.reference = reference if record else None
    case.escalation = escalation
    case.state = {
        **(case.state or {}),
        "facts": _jsonable(facts),
        "category_key": key,
        "reference": reference if record else None,
        "last_status": status,
        "turn": turn,
    }
    if status in ("resolved", "denied") and case.resolved_at is None:
        case.resolved_at = datetime.now(UTC)
    session.add(case)
    turn_cost = Decimal(case.cost_usd or 0) - cost_before

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
        "case_id": str(case.id),
        "decision_hash": decision_hash,
        "status_events": status_events,
        "options": ruling_payload["options"],
        "proactive": ruling_payload["proactive"],
        "queue": ruling_payload["queue"],
        "citations": [
            {
                k: c.get(k)
                for k in ("clause_id", "document", "document_version", "section", "page", "url", "text_hash", "quote")
            }
            for c in ruling_citations
            if c.get("public")
        ],
    }

    customer_block = {
        "status_lines": cx.status_lines(status_events, kind),
        "citations": ruling_payload["public_citations"],
        "options": ruling_payload["options"],
        "chosen_remedy": ruling_payload["chosen_remedy"],
        "proactive": ruling_payload["proactive"],
        "queue": (
            {"sla": cx.sla_sentence(sla_hours), "due_at": queue.get("due_at"), "overdue": bool(queue.get("overdue"))}
            if queue
            else None
        ),
    }
    ops = {
        "turn": turn,
        "customer": customer_block,
        "status": status,
        "classification": classification,
        "neighbours": neighbours,
        "plan": {
            "case_text": plan["case_text"],
            "historical": {k: v for k, v in plan["historical"].items()},
            "live": plan["live"],
            "comparison": plan["comparison"],
            "novel": plan["novel"],
            "chosen": plan["chosen"],
            "steps": plan["steps"],
            "disagreement": plan["disagreement"],
            "history_backs_ruling": plan.get("history_backs_ruling"),
        },
        "procedure": {
            "ref": proc_ref,
            "name": procedure.name if procedure else None,
            "steps": _step_table(procedure, facts, ruling, pack),
        },
        "record": {
            "reference": reference,
            "found": record is not None,
            "stand_in": stand_in,
            "summary": _record_summary(record, kind),
        },
        "facts": _jsonable(facts),
        "ruling": {
            **(ruling.model_dump(mode="json", exclude={"checks"}) if ruling else {}),
            "status": status,
            "clause_notes": clause_notes,
            "record_lookup": ruling_payload["record_lookup"],
            "citations": ruling_citations,
            "escalation": escalation,
            "gap": gap,
        },
        "citations": citations,
        "case": {
            "id": str(case.id),
            "status": status,
            "turns": case.turns,
            "event_count": case.event_count,
            "head_hash": case.head_hash,
            "decision_hash": decision_hash,
            "cost_usd": str(case.cost_usd),
            "turn_cost_usd": str(turn_cost),
            "llm_calls": case.llm_calls,
            "escalation": escalation,
        },
        "status_events": status_events,
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
            "decision_hash": decision_hash,
            "is_synthetic": ws.is_synthetic,
            "check": check_signal.status,
            "case_cost_usd": str(case.cost_usd),
            "turn_cost_usd": str(turn_cost),
            "llm_calls": case.llm_calls,
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
        "reference": reference if record else None,
        "missed_references": missed,
        "item_hint": extracted.get("item_hint") or state.get("item_hint"),
        "intents": intents,
        "party": party,
        "asked_facts": asked,
        "options": options,
        "chosen_remedy": chosen if options else None,
        "queue": queue,
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
    return {"reply": reply, "ops": ops, "customer": customer_block}


FRIENDLY = {
    "claim.has_photos": "photos of the damage or defect",
    "claim.has_receipt": "proof of purchase (order number or receipt)",
    "claim.item_unopened": "whether the item is still unopened in its original packaging",
    "claim.item_used": "whether the item has been used",
    "claim.requested_amount": "the amount you are claiming (with receipts)",
    "claim.reported_within_days": "when you first noticed the problem",
    "bag.checked": "whether the bag was checked in (including at the gate)",
    "bag.status": "what happened to the bag: damaged, delayed, lost or delivered",
    "bag.fee_paid": "the checked-bag fee you paid, if any",
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
    """Template reply (used when the drafter fails the supervisor twice): core sentence per status plus the
    customer-experience extras of design v2 §11-§14 (options, proactive answers, SLA)."""
    parts = [_fallback_core(rp)]
    q = rp.get("queue") or {}
    if rp["status"] == "pending_approval" and q.get("sla"):
        parts.append(f"You will hear back from us {q['sla']}.")
    opts = rp.get("options") or []
    if len(opts) > 1 and rp["status"] in ("resolved", "pending_approval"):
        first, rest = opts[0], opts[1:]
        lead = f"Your {first['label']}" + (f" of ${first['amount']}" if first.get("amount") else "")
        lead += " is guaranteed by regulation" if first.get("guaranteed_by_regulation") else " is the default"
        alts = "; ".join(o["label"] + (f" (${o['amount']})" if o.get("amount") else "") for o in rest)
        parts.append(f"{lead}; if you prefer, you may choose {alts} instead. Just reply with your choice.")
    for p in rp.get("proactive") or []:
        parts.append(p["answer"])
    return " ".join(x for x in parts if x)


def _fallback_core(rp: dict[str, Any]) -> str:
    st = rp["status"]
    lk = rp.get("record_lookup") or {}
    if st == "needs_fact" and lk.get("reference_given") and not lk.get("found"):
        return (
            f"Thanks for sending that. I could not find a record under {lk['reference_given']}, so it may have a typo. "
            "Could you double-check the reference on your itinerary or receipt and send it again? "
            "As soon as it matches I can confirm what we can do."
        )
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
    if st == "escalated" and (rp.get("queue") or {}).get("overdue"):
        return (
            "We are sorry: the authorisation we promised has taken longer than it should have. A specialist now owns "
            "your case and will follow up with you directly."
        )
    if st == "escalated" and lk.get("reference_given") and not lk.get("found"):
        return (
            f"Thanks for confirming. {lk['reference_given']} still does not match any record on our side, so rather than "
            "ask you again I have passed this to a colleague who will locate it from your name and travel or order "
            "date and follow up with you directly."
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
