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
