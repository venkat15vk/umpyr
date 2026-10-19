import json
from decimal import Decimal
from pathlib import Path

from umpyr_engine.ingest.loader import normalise, parse_rows
from umpyr_engine.learn.drift import compare
from umpyr_rules import Ruling


def test_parse_template_csv_and_normalise():
    rows = parse_rows(Path(__file__).resolve().parents[3] / "samples" / "ticket_template.csv")
    assert len(rows) == 1
    t = normalise(rows[0])
    assert t["ticket_id"] == "T-0001" and t["channel"] == "email" and t["order_id"] == "R-1001"
    assert t["thread"][0]["role"] == "customer" and t["thread"][1]["role"] == "staff"
    assert t["agent_actions"] == ["review_photos", "issue_replacement"]
    assert t["amount"] == Decimal("0.00")


def test_normalise_plain_text_thread_and_csv_list(tmp_path):
    t = normalise({"ticket_id": "X", "thread": "hello there", "agent_actions": "a, b", "amount": "$1,234.50"})
    assert t["thread"] == [{"role": "customer", "at": None, "text": "hello there"}]
    assert t["agent_actions"] == ["a", "b"] and t["amount"] == Decimal("1234.50")
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"ticket_id": "1", "thread": [{"author": "agent", "body": "hi"}]}) + "\n")
    assert normalise(parse_rows(p)[0])["thread"][0]["role"] == "staff"


def r(decision, remedy=None, amount=None):
    return Ruling(
        decision=decision,
        remedy=remedy,
        amount=Decimal(amount) if amount else None,
        winning_rule_id="R1",
        rule_pack_version="v",
    )


def test_compare_detects_drift_only_when_policy_decided():
    assert compare(r("deny", "none"), {"decision": "approve", "remedy": "refund"}) is not None
    assert compare(r("approve", "refund", "100"), {"decision": "deny"}) is not None
    assert compare(r("approve", "refund", "100"), {"decision": "approve", "remedy": "store_credit"}) is not None
    assert compare(r("approve", "refund", "100"), {"decision": "approve", "remedy": "refund", "amount": "100"}) is None
    assert (
        compare(r("approve", "refund", "100"), {"decision": "approve", "remedy": "refund", "amount": "150"}) is not None
    )
    assert compare(r("needs_approval", "refund"), {"decision": "deny"}) is None
    assert compare(r("escalate"), {"decision": "approve", "remedy": "refund"}) is None
    assert compare(r("deny"), {"decision": "escalate"}) is None
