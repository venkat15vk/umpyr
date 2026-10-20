"""Phase 3 runtime: refund options + choice, proactive refund timeline, queued-customer SLA, customer block."""

from __future__ import annotations

import json
import time
import uuid
from decimal import Decimal

import pytest
from test_resolve_runtime import FakeEmbedder, FakeLLM
from umpyr_engine.db import models as m
from umpyr_engine.llm.base import LLMRequest, LLMResponse, Usage
from umpyr_engine.resolve.ledger import CaseLedger
from umpyr_engine.resolve.runtime import run_turn
from umpyr_rules import Clause, Effect, Provenance, Rule, RulePack


class AirlineFakeLLM(FakeLLM):
    """Extraction for a delayed flight; optional remedy choice and timeline ask on later turns."""

    def __init__(self, requested_remedy: str = "refund", timeline_ask: str = "no"):
        super().__init__()
        self.requested_remedy = requested_remedy
        self.timeline_ask = timeline_ask

    def complete(self, req: LLMRequest) -> LLMResponse:
        if req.step == "resolve.classify":
            out = {
                "category_key": "delays",
                "confidence": 0.9,
                "multi_issue": False,
                "secondary_category_key": "",
                "rationale": "",
            }
        elif req.step == "resolve.extract":
            out = {
                "reference": "ABC123",
                "reference_kind": "confirmation_code",
                "item_hint": "",
                "intents": ["refund"],
                "party": {"pax_count": 1, "pax_types": ["adult"]},
                "timeline_ask": self.timeline_ask,
                "claim": {
                    "reason": "flight_delayed",
                    "requested_remedy": self.requested_remedy,
                    "requested_amount": -1,
                    "has_photos": "unknown",
                    "has_receipt": "yes",
                    "reported_within_days": -1,
                    "item_unopened": "unknown",
                    "item_used": "unknown",
                    "wants_credit_instead_of_refund": "yes" if self.requested_remedy == "travel_credit" else "unknown",
                    "description": "delayed",
                },
                "airline": {
                    "delay_minutes": 200,
                    "cause": "unknown",
                    "is_cancelled_by_airline": "no",
                    "customer_accepted_alternative": "no",
                    "customer_cancelled": "unknown",
                    "bag_minutes_to_carousel": -1,
                    "bag_report_filed": "unknown",
                    "bag_report_filed_within_hours": -1,
                    "bag_delay_days": -1,
                    "bag_damage_type": "",
                    "interim_expenses": -1,
                    "overnight_required": "unknown",
                },
            }
        else:
            return super().complete(req)
        self.calls.append(req.step)
        return LLMResponse(
            text=json.dumps(out),
            content=[],
            tool_uses=[],
            stop_reason="end_turn",
            usage=Usage(10, 10),
            model="fake",
            parsed=out,
        )


@pytest.fixture
def airline_ws(session_factory):
    prov = lambda doc, public=True: Provenance(
        document_id=doc, document_title=doc, document_version="a" * 64, text_hash="b" * 64, public=public
    )  # noqa: E731
    pack = RulePack(
        workspace="airws",
        version="a1",
        clauses=[
            Clause(
                clause_id="A-LAW-1",
                title="DOT refund",
                text="Refunds to a credit card must be issued within seven business days of the request.",
                source_url="https://law/260",
                tier="law",
                provenance=prov("US DOT 14 CFR 260"),
            ),
            Clause(
                clause_id="A-CRED-1",
                title="Credit",
                text="A travel credit may be offered instead of a refund.",
                source_url="https://coc",
                tier="commitment",
                provenance=prov("Customer Commitment"),
            ),
        ],
        rules=[
            Rule(
                rule_id="A-LAW-1.r1",
                clause_id="A-LAW-1",
                tier="law",
                claim_reasons=["flight_delayed"],
                requires=["flight.delay_minutes", "booking.total_paid"],
                when={">=": [{"var": "flight.delay_minutes"}, 180]},
                effect=Effect(decision="approve", remedy="refund", amount={"var": "booking.total_paid"}),
            ),
            Rule(
                rule_id="A-CRED-1.r1",
                clause_id="A-CRED-1",
                tier="commitment",
                claim_reasons=["flight_delayed"],
                requires=["flight.delay_minutes", "booking.total_paid"],
                when={">=": [{"var": "flight.delay_minutes"}, 120]},
                effect=Effect(
                    decision="approve", remedy="travel_credit", amount={"*": [{"var": "booking.total_paid"}, 1.1]}
                ),
            ),
        ],
    )
    with session_factory() as s:
        if s.get(m.Workspace, "airws") is None:
            s.add(
                m.Workspace(
                    id="airws",
                    name="Air",
                    kind="sandbox",
                    settings={"as_of": "2026-10-01T12:00:00", "record_kind": "booking", "approval_sla_hours": 4},
                )
            )
            s.flush()
            s.add(
                m.RulePack(
                    workspace_id="airws",
                    version="a1",
                    pack=json.loads(pack.model_dump_json()),
                    rule_count=2,
                    status="active",
                )
            )
            s.add(m.Category(workspace_id="airws", key="delays", name="Delays", description="", status="approved"))
            s.add(
                m.NextQuestion(
                    workspace_id="airws",
                    category_key="delays",
                    question="timeline",
                    probability=Decimal("0.6"),
                    support=6,
                )
            )
            s.add(
                m.OrderRecord(
                    workspace_id="airws",
                    order_id="ABC123",
                    kind="booking",
                    data={
                        "pnr": "ABC123",
                        "total_paid": "60.00",
                        "payment_method": "card",
                        "fare_class": "main",
                        "is_refundable_fare": False,
                        "purchased_at": "2026-09-01T10:00:00Z",
                        "flights": [
                            {
                                "flight_number": "AS1",
                                "origin": "SEA",
                                "destination": "JFK",
                                "scheduled_departure": "2026-10-02T12:00:00Z",
                                "status": "delayed",
                            }
                        ],
                        "bags": [],
                        "customer": {"customer_id": "C-9", "loyalty_member": False},
                    },
                )
            )
            s.commit()
    yield "airws"


def _chat(s, ws, limit="100"):
    chat = m.ChatSession(
        workspace_id=ws, channel="email", mode="autonomous", auto_approve_limit=Decimal(limit), state={}
    )
    s.add(chat)
    s.commit()
    return chat


def test_options_offered_cash_first_then_customer_chooses(session_factory, airline_ws):
    emb = FakeEmbedder()
    with session_factory() as s:
        chat = _chat(s, airline_ws)
        out = run_turn(s, AirlineFakeLLM(), emb, chat, "Flight ABC123 was delayed 200 minutes, I want a refund.")
        ops, cust = out["ops"], out["customer"]
        assert (
            ops["status"] == "resolved" and ops["ruling"]["remedy"] == "refund" and ops["ruling"]["amount"] == "60.00"
        )
        assert [o["remedy"] for o in cust["options"]] == ["refund", "travel_credit"]
        assert cust["options"][0]["guaranteed_by_regulation"] is True and cust["chosen_remedy"] is None
        assert cust["citations"][0]["document"] == "US DOT 14 CFR 260" and "clause_id" not in cust["citations"][0]
        assert "Received your message" in cust["status_lines"] and "Done" in cust["status_lines"]
        # proactive timeline: predicted next question (p=0.6) + refund ruling -> cited DOT window
        assert (
            cust["proactive"]
            and cust["proactive"][0]["question"] == "timeline"
            and "7 business days" in cust["proactive"][0]["answer"]
        )
        case_id = uuid.UUID(ops["case"]["id"])
        actions = [e.action for e in s.query(m.CaseEvent).filter_by(case_id=case_id).order_by(m.CaseEvent.seq)]
        assert "options_offered" in actions and "prefetch" in actions and "choice" not in actions
        # the customer picks the travel credit: ruling re-binds to the commitment rule, choice is logged
        out2 = run_turn(
            s, AirlineFakeLLM(requested_remedy="travel_credit"), emb, chat, "I'll take the travel credit instead."
        )
        ops2, cust2 = out2["ops"], out2["customer"]
        assert ops2["ruling"]["remedy"] == "travel_credit" and ops2["ruling"]["winning_rule_id"] == "A-CRED-1.r1"
        assert Decimal(ops2["ruling"]["amount"]) == Decimal("66.00") and cust2["chosen_remedy"] == "travel_credit"
        events = s.query(m.CaseEvent).filter_by(case_id=case_id).order_by(m.CaseEvent.seq).all()
        choice = next(e for e in events if e.action == "choice")
        assert choice.actor == "customer" and choice.result["chosen"] == "travel_credit"
        assert CaseLedger.verify(s, case_id)["ok"]
        assert ops2["agent_message"] is None  # email channel


def test_queued_customer_gets_sla_then_honest_overdue_escalation(session_factory, airline_ws):
    emb = FakeEmbedder()
    with session_factory() as s:
        ws = s.get(m.Workspace, airline_ws)
        ws.settings = {**ws.settings, "approval_sla_hours": 0.00002}  # ~70 ms
        s.add(ws)
        s.commit()
        try:
            chat = _chat(s, airline_ws, limit="10")  # $60 refund over a $10 limit -> queued
            out = run_turn(s, AirlineFakeLLM(), emb, chat, "Flight ABC123 was delayed 200 minutes, I want a refund.")
            assert out["ops"]["status"] == "pending_approval"
            q = out["customer"]["queue"]
            assert (
                q
                and q["sla"] == "within 0 minutes"
                or q["sla"].startswith("within")
                and q["due_at"]
                and q["overdue"] is False
            )
            case = s.get(m.ResolveCase, uuid.UUID(out["ops"]["case"]["id"]))
            assert case.due_at is not None and case.status == "pending_approval"
            time.sleep(0.2)
            out2 = run_turn(s, AirlineFakeLLM(), emb, chat, "Any update?")
            assert out2["ops"]["status"] == "escalated"
            esc = out2["ops"]["ruling"]["escalation"]
            assert esc["type"] == "governance_gate" and esc.get("overdue") is True and "SLA" in esc["reason"]
            assert out2["customer"]["queue"]["overdue"] is True
            assert "Authorisation took longer than promised" in out2["customer"]["status_lines"]
            actions = [e.action for e in s.query(m.CaseEvent).filter_by(case_id=case.id).order_by(m.CaseEvent.seq)]
            assert "queue.overdue" in actions
            s.refresh(case)
            assert case.status == "escalated" and case.due_at is None
        finally:
            ws = s.get(m.Workspace, airline_ws)
            ws.settings = {**ws.settings, "approval_sla_hours": 4}
            s.add(ws)
            s.commit()


def test_pending_message_without_sla_and_fallback_text(session_factory, airline_ws):
    from umpyr_engine.resolve.runtime import _fallback_reply

    rp = {
        "status": "pending_approval",
        "remedy": "refund",
        "amount": "60.00",
        "policy_reason": "x",
        "missing_facts": [],
        "record_lookup": {},
        "queue": {"sla": "within 4 hours", "overdue": False},
        "options": [
            {
                "remedy": "refund",
                "label": "refund to the original form of payment",
                "amount": "60.00",
                "guaranteed_by_regulation": True,
            },
            {"remedy": "travel_credit", "label": "travel credit", "amount": "66.00", "guaranteed_by_regulation": False},
        ],
        "proactive": [
            {"question": "timeline", "answer": "Refunds to your original card are issued within 7 business days."}
        ],
    }
    text = _fallback_reply(rp)
    assert "within 4 hours" in text and "guaranteed by regulation" in text and "travel credit ($66.00)" in text
    assert text.endswith("within 7 business days.")
    overdue = _fallback_reply({**rp, "status": "escalated", "queue": {"sla": "within 4 hours", "overdue": True}})
    assert "longer than it should have" in overdue


class BagFakeLLM(AirlineFakeLLM):
    """Damaged checked stroller, unknown confirmation code, no explicit bag_checked/bag_status (derivation path)."""

    def complete(self, req: LLMRequest) -> LLMResponse:
        if req.step != "resolve.extract":
            return super().complete(req)
        out = {
            "reference": "ZZZ999",
            "reference_kind": "confirmation_code",
            "item_hint": "stroller",
            "intents": ["compensation", "bag"],
            "party": {"pax_count": -1, "pax_types": []},
            "timeline_ask": "no",
            "claim": {
                "reason": "bag_damaged",
                "requested_remedy": "unknown",
                "requested_amount": -1,
                "has_photos": "unknown",
                "has_receipt": "yes",
                "reported_within_days": -1,
                "item_unopened": "unknown",
                "item_used": "unknown",
                "wants_credit_instead_of_refund": "unknown",
                "description": "stroller lost a wheel",
            },
            "airline": {
                "delay_minutes": -1,
                "cause": "unknown",
                "is_cancelled_by_airline": "unknown",
                "customer_accepted_alternative": "unknown",
                "customer_cancelled": "unknown",
                "bag_minutes_to_carousel": -1,
                "bag_report_filed": "unknown",
                "bag_report_filed_within_hours": -1,
                "bag_delay_days": -1,
                "bag_damage_type": "wheel",
                "interim_expenses": -1,
                "overnight_required": "unknown",
            },
        }
        self.calls.append(req.step)
        return LLMResponse(
            text=json.dumps(out),
            content=[],
            tool_uses=[],
            stop_reason="end_turn",
            usage=Usage(10, 10),
            model="fake",
            parsed=out,
        )


def test_damaged_bag_with_unknown_code_resolves_without_re_asking(session_factory, airline_ws):
    """The bug of 2026-10-20: bag.checked and bag.status only lived on the merchant record, so a stand-in record
    without a bag made the engine ask the same two questions forever. Now they derive from the claim."""
    from umpyr_rules import Clause, Effect, Rule, RulePack

    with session_factory() as s:
        rp = s.query(m.RulePack).filter_by(workspace_id=airline_ws, status="active").one()
        pack = RulePack.model_validate(rp.pack)
        if not any(r.rule_id == "A-BAG-1.r1" for r in pack.rules):
            pack.clauses.append(
                Clause(
                    clause_id="A-BAG-1",
                    title="Damaged bags",
                    text="Damaged checked bags are repaired or compensated.",
                    source_url="https://coc",
                    tier="contract",
                )
            )
            pack.rules.append(
                Rule(
                    rule_id="A-BAG-1.r1",
                    clause_id="A-BAG-1",
                    tier="contract",
                    claim_reasons=["bag_damaged"],
                    requires=["bag.checked", "bag.status", "bag.damage_type"],
                    when={"and": [{"==": [{"var": "bag.checked"}, True]}, {"==": [{"var": "bag.status"}, "damaged"]}]},
                    effect=Effect(decision="approve", remedy="voucher", amount=50),
                )
            )
            rp.pack = json.loads(pack.model_dump_json())
            s.add(rp)
            s.commit()
        chat = _chat(s, airline_ws)
        out = run_turn(
            s, BagFakeLLM(), FakeEmbedder(), chat, "My stroller lost a wheel on the flight, confirmation ZZZ999."
        )
        ops = out["ops"]
        assert ops["record"]["found"] and ops["record"]["stand_in"]
        assert (
            ops["facts"]["bag"]["checked"] is True
            and ops["facts"]["bag"]["status"] == "damaged"
            and ops["facts"]["bag"]["damage_type"] == "wheel"
        )
        assert ops["status"] == "resolved" and ops["ruling"]["remedy"] == "voucher", ops["ruling"]
        rec = s.query(m.OrderRecord).filter_by(workspace_id=airline_ws, order_id="ZZZ999").one()
        assert rec.data["bags"][0]["status"] == "damaged" and rec.data["bags"][0]["checked"] is True


class WheelFakeLLM(BagFakeLLM):
    """Wheel damage (excluded) and, on later turns, a new ask for credit."""

    def __init__(self, later_intents: list[str] | None = None):
        super().__init__()
        self.later_intents = later_intents or ["compensation"]
        self.turns = 0

    def complete(self, req: LLMRequest) -> LLMResponse:
        resp = super().complete(req)
        if req.step == "resolve.extract":
            self.turns += 1
            out = json.loads(resp.text)
            if self.turns > 1:
                out["intents"] = self.later_intents
                out["claim"]["requested_remedy"] = "travel_credit"
            resp = LLMResponse(
                text=json.dumps(out),
                content=[],
                tool_uses=[],
                stop_reason="end_turn",
                usage=Usage(10, 10),
                model="fake",
                parsed=out,
            )
        return resp


def _ensure_wheel_deny_rule(s, ws):
    from umpyr_rules import Clause, Effect, Rule, RulePack

    rp = s.query(m.RulePack).filter_by(workspace_id=ws, status="active").one()
    pack = RulePack.model_validate(rp.pack)
    if not any(r.rule_id == "A-BAG-9.r1" for r in pack.rules):
        pack.clauses.append(
            Clause(
                clause_id="A-BAG-9",
                title="Excluded parts",
                text="Not liable for wheels, handles or zippers.",
                source_url="https://coc",
                tier="contract",
            )
        )
        pack.rules.append(
            Rule(
                rule_id="A-BAG-9.r1",
                clause_id="A-BAG-9",
                tier="contract",
                priority=5,
                claim_reasons=["bag_damaged"],
                requires=["bag.damage_type"],
                when={"==": [{"var": "bag.damage_type"}, "wheel"]},
                effect=Effect(decision="deny"),
            )
        )
        rp.pack = json.loads(pack.model_dump_json())
        s.add(rp)
        s.commit()


def _seed_goodwill_history(session_factory, ws, n):
    with session_factory() as s:
        for i in range(n):
            s.add(
                m.Case(
                    workspace_id=ws,
                    ticket_id=f"GW-{i}",
                    category_key="delays",
                    summary="wheel broke; staff gave a voucher",
                    facts={"claim": {"reason": "bag_damaged"}},
                    record={
                        "policy": {"decision": "deny"},
                        "outcome": {"decision": "approve", "remedy": "voucher", "amount": 50},
                        "staff_actions": [],
                    },
                    is_drift=True,
                )
            )
        for i in range(3):
            s.add(
                m.Case(
                    workspace_id=ws,
                    ticket_id=f"DN-{i}",
                    category_key="delays",
                    summary="wheel broke; denied",
                    facts={"claim": {"reason": "bag_damaged"}},
                    record={
                        "policy": {"decision": "deny"},
                        "outcome": {"decision": "deny", "remedy": None},
                        "staff_actions": [],
                    },
                )
            )
        s.commit()


def _clear_cases(session_factory, ws):
    from sqlalchemy import delete

    with session_factory() as s:
        s.execute(delete(m.Case).where(m.Case.workspace_id == ws))
        s.commit()


def test_uncovered_ask_with_precedent_escalates_with_evidence(session_factory, airline_ws):
    _seed_goodwill_history(session_factory, airline_ws, n=2)
    try:
        with session_factory() as s:
            _ensure_wheel_deny_rule(s, airline_ws)
            chat = _chat(s, airline_ws)
            llm, emb = WheelFakeLLM(later_intents=["refund"]), FakeEmbedder()
            o1 = run_turn(s, llm, emb, chat, "My stroller lost a wheel, confirmation ZZZ999.")["ops"]
            assert o1["status"] == "denied" and o1["turn_delta"]["turn"] == 1
            o2 = run_turn(s, llm, emb, chat, "What else can you do? Can you give me credits?")["ops"]
            d = o2["turn_delta"]
            assert d["intents_added"] == ["refund"] and d["pushback"] is True and d["message_category"] is not None
            assert d["precedent"]["count"] == 2 and d["precedent"]["denied"] == 5 and d["precedent"]["exists"] is True
            assert o2["status"] == "escalated"
            esc = o2["ruling"]["escalation"]
            assert (
                esc["type"] == "policy_gap"
                and "goodwill in 2 of 5" in esc["reason"]
                and esc["precedent"]["remedies"] == {"voucher": 2}
            )
            assert (
                o2["ruling"]["decision"] == "deny"
            )  # the policy ruling itself is unchanged; a person decides goodwill
            actions = [
                e.action
                for e in s.query(m.CaseEvent).filter_by(case_id=uuid.UUID(o2["case"]["id"])).order_by(m.CaseEvent.seq)
            ]
            assert "precedent.goodwill" in actions
    finally:
        _clear_cases(session_factory, airline_ws)


def test_uncovered_ask_without_precedent_offers_review_then_escalates_on_second_pushback(session_factory, airline_ws):
    with session_factory() as s:
        _ensure_wheel_deny_rule(s, airline_ws)
        chat = _chat(s, airline_ws)
        llm, emb = WheelFakeLLM(), FakeEmbedder()
        o1 = run_turn(s, llm, emb, chat, "My stroller lost a wheel, confirmation ZZZ999.")["ops"]
        assert o1["status"] == "denied"
        o2 = run_turn(s, llm, emb, chat, "Can you give me credits?")["ops"]
        assert o2["status"] == "denied" and o2["turn_delta"]["uncovered_ask"]["offer_review"] is True
        assert o2["turn_delta"]["precedent"]["exists"] is False
        o3 = run_turn(s, llm, emb, chat, "I really think you should do something.")["ops"]
        assert o3["status"] == "escalated" and o3["ruling"]["escalation"]["type"] == "policy_gap"
        assert "pushed back 2 times" in o3["ruling"]["escalation"]["reason"]
        assert chat.state["pushbacks"] == 2


class OptionsFakeLLM(AirlineFakeLLM):
    """'What are my options?' (no choice made), then a rebooking choice on the next turn."""

    def __init__(self):
        super().__init__(requested_remedy="unknown")
        self.turns = 0

    def complete(self, req: LLMRequest) -> LLMResponse:
        resp = super().complete(req)
        if req.step == "resolve.extract":
            self.turns += 1
            out = json.loads(resp.text)
            out["intents"] = ["status"] if self.turns == 1 else ["rebook"]
            out["airline"]["customer_accepted_alternative"] = "unknown" if self.turns == 1 else "yes"
            out["claim"]["requested_remedy"] = "unknown" if self.turns == 1 else "rebook"
            resp = LLMResponse(
                text=json.dumps(out),
                content=[],
                tool_uses=[],
                stop_reason="end_turn",
                usage=Usage(10, 10),
                model="fake",
                parsed=out,
            )
        return resp


def _ensure_choice_rules(s, ws):
    from umpyr_rules import Clause, Effect, Rule, RulePack

    rp = s.query(m.RulePack).filter_by(workspace_id=ws, status="active").one()
    pack = RulePack.model_validate(rp.pack)
    changed = False
    for r in pack.rules:
        if r.rule_id == "A-LAW-1.r1" and "flight.customer_accepted_alternative" not in r.requires:
            r.requires = [*r.requires, "flight.customer_accepted_alternative"]
            r.when = {
                "and": [
                    {">=": [{"var": "flight.delay_minutes"}, 180]},
                    {"==": [{"var": "flight.customer_accepted_alternative"}, False]},
                ]
            }
            changed = True
    if not any(r.rule_id == "A-REB-1.r1" for r in pack.rules):
        pack.clauses.append(
            Clause(
                clause_id="A-REB-1",
                title="Rebooking",
                text="During a long delay you may be rebooked on the next available flight.",
                source_url="https://coc",
                tier="commitment",
            )
        )
        pack.rules.append(
            Rule(
                rule_id="A-REB-1.r1",
                clause_id="A-REB-1",
                tier="commitment",
                priority=5,
                claim_reasons=["flight_delayed"],
                requires=["flight.delay_minutes", "flight.customer_accepted_alternative"],
                when={
                    "and": [
                        {">=": [{"var": "flight.delay_minutes"}, 180]},
                        {"==": [{"var": "flight.customer_accepted_alternative"}, True]},
                    ]
                },
                effect=Effect(decision="approve", remedy="rebook"),
            )
        )
        changed = True
    if changed:
        rp.pack = json.loads(pack.model_dump_json())
        s.add(rp)
        s.commit()


def test_what_are_my_options_presents_choices_then_rebooking_is_granted(session_factory, airline_ws):
    """The NY→SEA delay of 2026-10-20: the engine assumed the customer declined travel and queued a refund.
    Now a choice only the customer can make is presented as options and nothing is queued."""
    emb = FakeEmbedder()
    with session_factory() as s:
        _ensure_choice_rules(s, airline_ws)
        chat = _chat(s, airline_ws, limit="10")  # a refund would need authorisation; it must NOT be queued yet
        llm = OptionsFakeLLM()
        out = run_turn(s, llm, emb, chat, "Flight ABC123 keeps getting delayed, now 200 minutes. What are my options?")
        ops, cust = out["ops"], out["customer"]
        assert ops["status"] == "needs_choice" and cust["queue"] is None
        keys = [c["key"] for c in cust["choices"]]
        assert keys == ["refund", "rebook"]  # regulated cash first
        refund = cust["choices"][0]
        assert refund["guaranteed_by_regulation"] and refund["remedy"] == "refund" and refund["amount"] == "60.00"
        assert cust["choices"][1]["remedy"] == "rebook" and cust["choices"][1]["decision"] == "approve"
        case = s.get(m.ResolveCase, uuid.UUID(ops["case"]["id"]))
        assert case.status == "needs_choice" and case.due_at is None
        actions = [e.action for e in s.query(m.CaseEvent).filter_by(case_id=case.id).order_by(m.CaseEvent.seq)]
        assert "choices_offered" in actions and "gate" in actions
        assert "Laid out your options" in cust["status_lines"]
        # the customer chooses rebooking: the commitment rule applies, no refund is queued
        out2 = run_turn(s, llm, emb, chat, "Put me on the morning flight please.")
        ops2 = out2["ops"]
        assert (
            ops2["status"] == "resolved"
            and ops2["ruling"]["remedy"] == "rebook"
            and ops2["ruling"]["winning_rule_id"] == "A-REB-1.r1"
        )
        assert ops2["facts"]["flight"]["customer_accepted_alternative"] is True
        events = s.query(m.CaseEvent).filter_by(case_id=case.id).order_by(m.CaseEvent.seq).all()
        choice = [e for e in events if e.action == "choice"]
        assert (
            choice
            and choice[-1].actor == "customer"
            and choice[-1].result == {"fact": "flight.customer_accepted_alternative", "value": True}
        )
        assert CaseLedger.verify(s, case.id)["ok"]


def test_small_talk_turn_is_acknowledged_without_re_evaluation(session_factory, airline_ws):
    emb = FakeEmbedder()
    with session_factory() as s:
        chat = _chat(s, airline_ws, limit="10")
        llm = AirlineFakeLLM()
        out = run_turn(s, llm, emb, chat, "Flight ABC123 was delayed 200 minutes, I want a refund.")
        assert out["ops"]["status"] == "pending_approval"
        n_calls = len(llm.calls)
        out2 = run_turn(s, llm, emb, chat, "Good, I like you")
        assert len(llm.calls) == n_calls  # no model call at all
        assert out2["ops"]["status"] == "pending_approval" and out2["ops"]["turn_delta"]["no_ask"] is True
        assert "You're welcome" in out2["reply"] and "within 4 hours" in out2["reply"]
        assert out2["customer"]["queue"] is None and out2["customer"]["status_lines"] == ["Received your message"]
        events = (
            s.query(m.CaseEvent).filter_by(case_id=uuid.UUID(out["ops"]["case"]["id"])).order_by(m.CaseEvent.seq).all()
        )
        assert events[-1].action == "acknowledgement" and events[-1].turn == 2
        assert CaseLedger.verify(s, events[-1].case_id)["ok"]
        assert chat.state["turn"] == 2 and chat.state["last_status"] == "pending_approval"
