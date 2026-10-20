"""Resolve runtime with a fake LLM: deterministic end-to-end turn over a tiny workspace (no network, no spend)."""

from __future__ import annotations

import dataclasses
import json
from decimal import Decimal

import pytest
from umpyr_engine.db import models as m
from umpyr_engine.llm.base import LLMRequest, LLMResponse, Usage
from umpyr_engine.resolve.runtime import run_turn
from umpyr_rules import Clause, Effect, Rule, RulePack


class FakeLLM:
    """Routes by request step; returns canned structured JSON."""

    name = "fake"

    def __init__(self):
        self.calls = []

    def complete(self, req: LLMRequest) -> LLMResponse:
        self.calls.append(req.step)
        user = req.messages[-1]["content"] if req.messages else ""
        if req.step == "resolve.classify":
            out = {
                "category_key": "returns",
                "confidence": 0.9,
                "multi_issue": False,
                "secondary_category_key": "",
                "rationale": "return",
            }
        elif req.step == "resolve.extract":
            photos = "yes" if "photos" in user.lower() and "attached" in user.lower() else "unknown"
            out = {
                "reference": "R-900001",
                "item_hint": "clamp",
                "claim": {
                    "reason": "damaged_on_arrival",
                    "requested_remedy": "replacement",
                    "requested_amount": -1,
                    "has_photos": photos,
                    "has_receipt": "yes",
                    "reported_within_days": -1,
                    "item_unopened": "unknown",
                    "item_used": "unknown",
                    "wants_credit_instead_of_refund": "unknown",
                    "description": "arrived broken",
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
                    "bag_damage_type": "",
                    "interim_expenses": -1,
                    "overnight_required": "unknown",
                },
            }
        elif req.step == "resolve.draft":
            payload = json.loads(user)
            out = {
                "reply": f"[{payload['ruling']['status']}] "
                + (
                    ", ".join(payload["ruling"]["missing_facts"])
                    or f"{payload['ruling']['remedy']} {payload['ruling']['amount']}"
                )
            }
        elif req.step == "resolve.supervise":
            out = {"approved": True, "issues": []}
        else:
            out = {}
        text = json.dumps(out)
        return LLMResponse(
            text=text, content=[], tool_uses=[], stop_reason="end_turn", usage=Usage(10, 10), model="fake", parsed=out
        )

    def batch(self, reqs, poll_seconds=0):
        return [self.complete(r) for r in reqs]


class FakeEmbedder:
    name = "fake"
    dim = 1024

    def embed(self, texts, *, step, workspace_id, input_type="document"):
        from umpyr_engine.embeddings.local import LocalHashEmbedder

        return LocalHashEmbedder().embed(texts, step=step, workspace_id=workspace_id)


@pytest.fixture
def tiny_workspace(session_factory):
    pack = RulePack(
        workspace="tinyws",
        version="t1",
        clauses=[
            Clause(
                clause_id="T-DMG-1",
                title="Damage",
                text="Damaged items are replaced when photos are provided.",
                source_url="https://example.com/p",
                tier="contract",
            )
        ],
        rules=[
            Rule(
                rule_id="T-DMG-1.r1",
                clause_id="T-DMG-1",
                tier="contract",
                claim_reasons=["damaged_on_arrival"],
                requires=["claim.has_photos", "item.line_total"],
                when={"==": [{"var": "claim.has_photos"}, True]},
                effect=Effect(
                    decision="approve",
                    remedy="replacement",
                    amount={"var": "item.line_total"},
                    fault="carrier",
                    note="Replacement with photos",
                ),
            ),
            Rule(
                rule_id="T-DMG-1.r2",
                clause_id="T-DMG-1",
                tier="contract",
                claim_reasons=["damaged_on_arrival"],
                requires=["claim.has_photos"],
                when={"==": [{"var": "claim.has_photos"}, False]},
                effect=Effect(decision="needs_fact", note="Photos required"),
            ),
        ],
    )
    with session_factory() as s:
        if s.get(m.Workspace, "tinyws") is None:
            s.add(
                m.Workspace(
                    id="tinyws",
                    name="Tiny",
                    kind="sandbox",
                    settings={"as_of": "2026-10-01T12:00:00", "record_kind": "order"},
                )
            )
            s.flush()
            s.add(
                m.RulePack(
                    workspace_id="tinyws",
                    version="t1",
                    pack=json.loads(pack.model_dump_json()),
                    rule_count=2,
                    status="active",
                )
            )
            s.add(
                m.Category(
                    workspace_id="tinyws", key="returns", name="Returns", description="returns", status="approved"
                )
            )
            s.add(
                m.Procedure(
                    workspace_id="tinyws",
                    category_key="returns",
                    version=1,
                    name="Damage procedure",
                    status="approved",
                    steps=[
                        {
                            "step_id": "returns.s1",
                            "name": "Gather photos",
                            "kind": "gather",
                            "instruction": "",
                            "required_facts": ["claim.has_photos"],
                            "clause_ids": ["T-DMG-1"],
                            "tool": "extract_facts",
                        },
                        {
                            "step_id": "returns.s2",
                            "name": "Decide",
                            "kind": "decide",
                            "instruction": "",
                            "required_facts": [],
                            "clause_ids": ["T-DMG-1"],
                            "tool": "rule",
                        },
                    ],
                    required_facts=["claim.has_photos"],
                )
            )
            s.add(
                m.OrderRecord(
                    workspace_id="tinyws",
                    order_id="R-900001",
                    kind="order",
                    data={
                        "order_id": "R-900001",
                        "placed_at": "2026-09-20T10:00:00Z",
                        "status": "delivered",
                        "total": "60.00",
                        "items": [
                            {
                                "item_id": "L1",
                                "sku": "C1",
                                "name": "Bar clamp",
                                "category": "hardware",
                                "line_total": "45.00",
                                "price": "45.00",
                                "quantity": 1,
                            }
                        ],
                        "shipments": [
                            {"shipment_id": "S1", "delivered_at": "2026-09-25T10:00:00Z", "status": "delivered"}
                        ],
                        "customer": {"customer_id": "C-1", "loyalty_member": False},
                    },
                )
            )
            s.commit()
    yield "tinyws"


def test_turn_asks_for_missing_fact_then_rules_and_audits(session_factory, tiny_workspace):
    llm, emb = FakeLLM(), FakeEmbedder()
    with session_factory() as s:
        chat = m.ChatSession(
            workspace_id=tiny_workspace, channel="email", mode="autonomous", auto_approve_limit=Decimal("100"), state={}
        )
        s.add(chat)
        s.commit()
        out = run_turn(s, llm, emb, chat, "My clamp from order R-900001 arrived broken. Email me at bob@example.com")
        ops = out["ops"]
        assert ops["status"] == "needs_fact"
        assert ops["ruling"]["missing_facts"] == ["claim.has_photos"]
        assert "[EMAIL]" in chat.state["history"][0]["text"]  # masked before storage
        assert ops["record"]["found"] and ops["record"]["reference"] == "R-900001"
        assert ops["procedure"]["steps"][0]["status"] == "needs_fact"
        assert ops["governance"]["audit_hash"] and len(ops["governance"]["audit_hash"]) == 64
        assert [t["tool"] for t in ops["tools"]] == [
            "classify",
            "find_neighbours",
            "get_procedure",
            "lookup_order",
            "extract_facts",
            "rule",
            "authorize",
            "draft_reply",
            "supervisor",
        ]

        out2 = run_turn(s, llm, emb, chat, "Sure, photos attached.")
        ops2 = out2["ops"]
        assert (
            ops2["status"] == "resolved"
            and ops2["ruling"]["decision"] == "approve"
            and ops2["ruling"]["remedy"] == "replacement"
        )
        assert ops2["ruling"]["amount"] == "45.00"
        assert ops2["authorization"]["decision"] == "allow"
        assert ops2["turn"] == 2 and chat.status == "resolved"
        assert "replacement" in out2["reply"]
        recs = s.query(m.RulingRecord).filter_by(session_id=chat.id).all()
        assert len(recs) == 2 and recs[0].audit_hash != recs[1].audit_hash
        assert "resolve.classify" in llm.calls and "resolve.supervise" in llm.calls


def test_modes_and_limit_route_through_pdp(session_factory, tiny_workspace):
    llm, emb = FakeLLM(), FakeEmbedder()
    with session_factory() as s:
        for mode, limit, expected in (
            ("assist", 100, "pending_approval"),
            ("shadow", 100, "pending_approval"),
            ("autonomous", 10, "pending_approval"),
            ("autonomous", 100, "resolved"),
        ):
            chat = m.ChatSession(
                workspace_id=tiny_workspace, channel="agent", mode=mode, auto_approve_limit=Decimal(limit), state={}
            )
            s.add(chat)
            s.commit()
            out = run_turn(s, llm, emb, chat, "Order R-900001 arrived broken, photos attached.")
            assert out["ops"]["status"] == expected, (mode, limit)
            msg = out["ops"]["agent_message"]
            assert msg["protocol"] == "umpyr-resolve/0.2" and msg["status"] == expected and msg["audit_hash"]


class MissingRefLLM(FakeLLM):
    """Extracts whatever order-looking token the customer typed, so lookups can miss."""

    def complete(self, req: LLMRequest) -> LLMResponse:
        if req.step == "resolve.extract":
            import re

            user = req.messages[-1]["content"] if req.messages else ""
            found = re.findall(r"R-\d{6}", user)
            res = super().complete(req)
            data = json.loads(res.text)
            data["reference"] = found[-1] if found else ""
            return dataclasses.replace(res, text=json.dumps(data), parsed=data)
        return super().complete(req)


def test_find_reference_accepts_short_keyword_codes():
    from umpyr_engine.resolve.facts import find_reference

    assert find_reference("Confirmation COde: XVZLT", "booking") == "XVZLT"
    assert find_reference("my record locator is ab12cd", "booking") == "AB12CD"
    assert find_reference("Please cancel my flight, thanks", "booking") is None
    assert find_reference("ok, i booked a ticket to New York and my flight is delayed", "booking") is None
    assert find_reference("fine, it is ZBFRKV", "booking") == "ZBFRKV"
    assert find_reference("my order arrived broken", "order") is None
    assert find_reference("order R-900001 arrived broken", "order") == "R-900001"


def test_unknown_reference_gets_a_stand_in_record_by_default(session_factory, tiny_workspace):
    llm, emb = MissingRefLLM(), FakeEmbedder()
    with session_factory() as s:
        chat = m.ChatSession(
            workspace_id=tiny_workspace, channel="email", mode="autonomous", auto_approve_limit=Decimal("100"), state={}
        )
        s.add(chat)
        s.commit()
        ops = run_turn(s, llm, emb, chat, "My clamp from order R-123456 arrived broken, photos attached.")["ops"]
        assert ops["record"]["found"] and ops["record"]["stand_in"] and ops["record"]["reference"] == "R-123456"
        assert ops["status"] == "resolved" and ops["ruling"]["remedy"] == "replacement"
        assert "stand-in" in next(t["detail"] for t in ops["tools"] if t["tool"] == "lookup_order")
        assert "order" in ops["facts"] and "stand_in" not in ops["facts"]["order"]
        rec = s.query(m.OrderRecord).filter_by(workspace_id=tiny_workspace, order_id="R-123456").one()
        assert rec.data["stand_in_template"] == "R-900001" and "name" not in rec.data["customer"]
        assert chat.state["reference"] == "R-123456"  # persisted: later turns reuse it


def test_unmatched_reference_is_named_not_sticky_and_escalates_after_three(
    session_factory, tiny_workspace, monkeypatch
):
    from umpyr_engine.config import get_settings

    monkeypatch.setenv("ASSUME_REFERENCE_EXISTS", "false")
    get_settings.cache_clear()
    llm, emb = MissingRefLLM(), FakeEmbedder()
    with session_factory() as s:
        chat = m.ChatSession(
            workspace_id=tiny_workspace, channel="email", mode="autonomous", auto_approve_limit=Decimal("100"), state={}
        )
        s.add(chat)
        s.commit()
        ops = run_turn(s, llm, emb, chat, "My clamp from order R-553456 arrived broken, photos attached.")["ops"]
        assert ops["status"] == "needs_fact"
        assert ops["record"] == {"reference": "R-553456", "found": False, "stand_in": False, "summary": None}
        assert "R-553456 did not match" in ops["ruling"]["missing_facts"][0]
        assert ops["ruling"]["record_lookup"]["unmatched_references"] == ["R-553456"]
        assert chat.state["reference"] is None  # a miss must not stick

        # the customer corrects the number: the new one is looked up, not the stale one
        ops2 = run_turn(s, llm, emb, chat, "Sorry, typo, it is R-900001. Photos attached.")["ops"]
        assert ops2["record"]["found"] and ops2["record"]["reference"] == "R-900001"
        assert ops2["status"] == "resolved"

    with session_factory() as s:
        chat = m.ChatSession(
            workspace_id=tiny_workspace, channel="email", mode="autonomous", auto_approve_limit=Decimal("100"), state={}
        )
        s.add(chat)
        s.commit()
        for ref in ("R-553456", "R-553457"):
            ops = run_turn(s, llm, emb, chat, f"Broken clamp, order {ref}, photos attached.")["ops"]
            assert ops["status"] == "needs_fact"
        ops = run_turn(s, llm, emb, chat, "Broken clamp, order R-553458, photos attached.")["ops"]
        assert ops["status"] == "escalated"
        assert "R-553456, R-553457, R-553458" in ops["ruling"]["explanation"]

    with session_factory() as s:  # the customer insists on the same bad reference: hand off, do not loop
        chat = m.ChatSession(
            workspace_id=tiny_workspace, channel="email", mode="autonomous", auto_approve_limit=Decimal("100"), state={}
        )
        s.add(chat)
        s.commit()
        assert run_turn(s, llm, emb, chat, "Broken clamp, order R-553456.")["ops"]["status"] == "needs_fact"
        ops = run_turn(s, llm, emb, chat, "I already told you, it is R-553456. Read my message.")["ops"]
        assert ops["status"] == "escalated"
        assert "confirmed R-553456 twice" in ops["ruling"]["explanation"]


class MeteredFakeLLM(FakeLLM):
    """FakeLLM that reports a cost to the per-case meter, as the real provider does."""

    def complete(self, req: LLMRequest) -> LLMResponse:
        from umpyr_engine.llm.meter import current

        resp = super().complete(req)
        meter = current()
        if meter:
            meter.add(req.step, 0.0005, 10, 10)
        return resp


def test_turn_writes_case_ledger_citations_and_cost(session_factory, tiny_workspace):
    import uuid

    from umpyr_engine.resolve.ledger import CaseLedger

    llm, emb = MeteredFakeLLM(), FakeEmbedder()
    with session_factory() as s:
        chat = m.ChatSession(
            workspace_id=tiny_workspace, channel="agent", mode="autonomous", auto_approve_limit=Decimal("100"), state={}
        )
        s.add(chat)
        s.commit()
        ops = run_turn(s, llm, emb, chat, "Order R-900001 arrived broken, photos attached.")["ops"]
        assert ops["status"] == "resolved"
        case = ops["case"]
        case_id = uuid.UUID(case["id"])
        assert case["head_hash"] and case["decision_hash"] and case["event_count"] >= 10 and case["turns"] == 1
        assert Decimal(case["cost_usd"]) > 0 and case["llm_calls"] == len(llm.calls)
        assert Decimal(ops["governance"]["turn_cost_usd"]) == Decimal(case["cost_usd"])
        # citations resolved from provenance (clause text hash, never model text)
        cits = ops["ruling"]["citations"]
        assert cits and cits[0]["clause_id"] == "T-DMG-1" and len(cits[0]["text_hash"]) == 64
        assert ops["citations"]["T-DMG-1"]["quote"].startswith("Damaged items") and cits[0]["public"] is True
        agent = ops["agent_message"]
        assert agent["case_id"] == case["id"] and agent["citations"][0]["clause_id"] == "T-DMG-1"
        assert agent["decision_hash"] == case["decision_hash"]
        assert ops["ruling"]["escalation"] is None and ops["governance"]["check"] == "not_configured"
        assert ops["status_events"][0]["action"] == "message.received"
        assert ops["status_events"][-1]["action"] == "decision.resolved"
        events = s.query(m.CaseEvent).filter_by(case_id=case_id).order_by(m.CaseEvent.seq).all()
        actions = [e.action for e in events]
        assert actions[:3] == ["case.opened", "message.received", "check.ingest"]
        for a in (
            "classification",
            "precedent.retrieved",
            "plan.selected",
            "record.lookup",
            "extraction",
            "rule_check",
            "check.pre_execution",
            "gate",
            "notification.drafted",
            "decision",
        ):
            assert a in actions, a
        assert actions[-1] == "decision"
        rule_ev = next(e for e in events if e.action == "rule_check")
        assert rule_ev.basis["citations"][0]["clause_id"] == "T-DMG-1" and rule_ev.basis["rule_pack_version"] == "t1"
        assert sum(Decimal(e.cost_usd) for e in events) == Decimal(case["cost_usd"])
        assert CaseLedger.verify(s, case_id)["ok"]
        # second turn appends to the same case and keeps the chain valid
        ops2 = run_turn(s, llm, emb, chat, "Thanks, that works.")["ops"]
        assert ops2["case"]["id"] == case["id"] and ops2["case"]["turns"] == 2
        assert ops2["case"]["event_count"] > case["event_count"]
        assert CaseLedger.verify(s, case_id)["ok"]
        row = s.get(m.ResolveCase, case_id)
        assert row.status == "resolved" and row.resolved_at is not None and row.category_key == "returns"


def test_escalations_are_typed(session_factory, tiny_workspace, monkeypatch):
    from umpyr_engine.config import get_settings

    llm, emb = FakeLLM(), FakeEmbedder()
    with session_factory() as s:  # over the auto-approve limit -> queued -> governance_gate
        chat = m.ChatSession(
            workspace_id=tiny_workspace, channel="email", mode="autonomous", auto_approve_limit=Decimal("10"), state={}
        )
        s.add(chat)
        s.commit()
        ops = run_turn(s, llm, emb, chat, "Order R-900001 arrived broken, photos attached.")["ops"]
        assert ops["status"] == "pending_approval"
        assert ops["ruling"]["escalation"]["type"] == "governance_gate"
        assert s.get(m.ResolveCase, __import__("uuid").UUID(ops["case"]["id"])).escalation["type"] == "governance_gate"
        assert any(e["action"] == "gate.queued" for e in ops["status_events"])
    monkeypatch.setenv("ASSUME_REFERENCE_EXISTS", "false")
    get_settings.cache_clear()
    llm = MissingRefLLM()  # extraction returns no reference, so an unknown order number stays unknown
    try:
        with session_factory() as s:  # record never found -> ask is a data gap, then escalation is a data gap
            chat = m.ChatSession(
                workspace_id=tiny_workspace,
                channel="email",
                mode="autonomous",
                auto_approve_limit=Decimal("100"),
                state={},
            )
            s.add(chat)
            s.commit()
            ops = run_turn(s, llm, emb, chat, "Broken clamp, order R-773456, photos attached.")["ops"]
            assert ops["status"] == "needs_fact" and ops["ruling"]["gap"]["type"] == "data_gap"
            ops = run_turn(s, llm, emb, chat, "It is R-773456, I am sure.")["ops"]
            assert ops["status"] == "escalated" and ops["ruling"]["escalation"]["type"] == "data_gap"
    finally:
        monkeypatch.delenv("ASSUME_REFERENCE_EXISTS", raising=False)
        get_settings.cache_clear()
