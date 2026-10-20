"""Case ledger: hash chain, verification, append-only trigger, idempotent steps, decision hash."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from umpyr_engine.db import models as m
from umpyr_engine.resolve.ledger import CaseLedger


@pytest.fixture
def chat(session_factory):
    with session_factory() as s:
        if s.get(m.Workspace, "ledgerws") is None:
            s.add(m.Workspace(id="ledgerws", name="Ledger", kind="sandbox", status="ready", is_synthetic=True))
            s.commit()
        c = m.ChatSession(workspace_id="ledgerws", state={})
        s.add(c)
        s.commit()
        return c.id


def test_chain_links_and_verifies(session_factory, chat):
    with session_factory() as s:
        chat_row = s.get(m.ChatSession, chat)
        ledger = CaseLedger.open(s, chat_row)
        ledger.begin_turn()
        e1 = ledger.append("umpyr", "classification", basis={"source": "router"}, result={"category_key": "x"})
        e2 = ledger.append(
            "umpyr", "rule_check", result={"decision": "approve"}, cost_usd=Decimal("0.0012"), llm_calls=2
        )
        s.commit()
        case_id = ledger.case.id
        assert e1.prev_hash is not None  # case.opened came first
        assert e2.prev_hash == e1.hash and e2.seq == e1.seq + 1
        assert ledger.case.head_hash == e2.hash and ledger.case.event_count == 3
        assert ledger.case.cost_usd == Decimal("0.001200") and ledger.case.llm_calls == 2
        # reopening the same session returns the same case, no new case.opened
        again = CaseLedger.open(s, chat_row)
        assert again.case.id == case_id and again.case.event_count == 3
        v = CaseLedger.verify(s, case_id)
        assert v["ok"] and v["events"] == 3 and v["broken_at"] is None


def test_decision_hash_commits_to_head(session_factory, chat):
    with session_factory() as s:
        ledger = CaseLedger.open(s, s.get(m.ChatSession, chat))
        ledger.append("umpyr", "decision", result={"status": "resolved"})
        d1 = ledger.commit_decision("a" * 64)
        d2 = ledger.commit_decision("b" * 64)
        assert d1 != d2 and len(d1) == 64
        ledger.append("umpyr", "decision", result={"status": "resolved"})
        assert ledger.commit_decision("a" * 64) != d1  # head moved, so the commitment moves


def test_events_are_append_only(session_factory, chat):
    with session_factory() as s:
        ledger = CaseLedger.open(s, s.get(m.ChatSession, chat))
        ev = ledger.append("umpyr", "extraction", result={"claim_keys": ["reason"]})
        s.commit()
        ev_id = ev.id
    with session_factory() as s:
        with pytest.raises(Exception, match="append-only"):
            s.execute(text("UPDATE case_events SET result = '{}'::jsonb WHERE id = :id"), {"id": ev_id})
            s.commit()
    with session_factory() as s:
        with pytest.raises(Exception, match="append-only"):
            s.execute(text("DELETE FROM case_events WHERE id = :id"), {"id": ev_id})
            s.commit()


def test_verify_detects_tampering_with_the_head(session_factory, chat):
    with session_factory() as s:
        ledger = CaseLedger.open(s, s.get(m.ChatSession, chat))
        ledger.append("umpyr", "extraction", result={"claim_keys": ["reason"]})
        s.commit()
        case_id = ledger.case.id
        case = s.get(m.ResolveCase, case_id)
        case.head_hash = "0" * 64
        s.commit()
        assert CaseLedger.verify(s, case_id)["ok"] is False


def test_retention_cascade_still_deletes_events(session_factory, chat):
    with session_factory() as s:
        ledger = CaseLedger.open(s, s.get(m.ChatSession, chat))
        ledger.append("umpyr", "extraction")
        s.commit()
        case_id = ledger.case.id
        s.delete(s.get(m.ResolveCase, case_id))
        s.commit()
        assert s.execute(text("SELECT count(*) FROM case_events WHERE case_id = :c"), {"c": case_id}).scalar() == 0


def test_ensure_once_is_idempotent_across_turns(session_factory, chat):
    with session_factory() as s:
        ledger = CaseLedger.open(s, s.get(m.ChatSession, chat))
        ledger.begin_turn()
        assert ledger.ensure_once("refund_party", "case:refund:R-1") is True
        ledger.begin_turn()
        assert ledger.ensure_once("refund_party", "case:refund:R-1") is False
        assert ledger.ensure_once("notify", "case:notify:1") is True
        s.commit()
        executed = s.get(m.ResolveCase, ledger.case.id).state["executed"]
        assert [e["step_key"] for e in executed] == ["refund_party", "notify"]
        assert executed[0]["turn"] == 1 and executed[1]["turn"] == 2


def test_unknown_actor_rejected(session_factory, chat):
    with session_factory() as s:
        ledger = CaseLedger.open(s, s.get(m.ChatSession, chat))
        with pytest.raises(ValueError):
            ledger.append("hacker", "x")
