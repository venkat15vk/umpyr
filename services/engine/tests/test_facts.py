"""Bag facts derived from the claim (regression for the 2026-10-20 stroller loop)."""

from __future__ import annotations

from datetime import datetime

from umpyr_engine.resolve.facts import build_facts, derive_bag_facts


def test_derive_bag_facts_from_reason_and_damage():
    assert derive_bag_facts({"reason": "bag_damaged"}, {}) == {"status": "damaged", "checked": True}
    assert derive_bag_facts({"reason": "bag_delayed"}, {"bag_delay_days": 2}) == {"status": "delayed", "checked": True}
    assert derive_bag_facts({"reason": "other"}, {"bag_damage_type": "wheel"}) == {
        "status": "damaged",
        "checked": True,
        "damage_type": "wheel",
    }
    assert derive_bag_facts({}, {"bag_damage_type": "missing wheel"})["damage_type"] == "wheel"
    assert derive_bag_facts({}, {"bag_damage_type": "cracked shell"})["damage_type"] == "structural"
    assert derive_bag_facts({"reason": "bag_guarantee"}, {}) == {"checked": True}
    assert derive_bag_facts({"reason": "flight_delayed"}, {"bag_damage_type": "none"}) == {}
    # explicit statements win
    assert derive_bag_facts({"reason": "bag_damaged"}, {"bag_status": "lost", "bag_checked": False}) == {
        "status": "lost",
        "checked": False,
    }


def test_build_facts_bag_section_with_and_without_record():
    at = datetime(2026, 10, 1, 12, 0)
    none = build_facts({"reason": "bag_damaged"}, {"bag_damage_type": "wheel"}, None, at, "booking")
    assert none["bag"] == {"status": "damaged", "checked": True, "damage_type": "wheel"}
    record = {
        "pnr": "X",
        "total_paid": "100",
        "flights": [{"flight_number": "AS1"}],
        "bags": [{"bag_tag": "T1", "status": "delivered", "checked": True, "damage_type": "none"}],
    }
    with_rec = build_facts({"reason": "bag_damaged"}, {"bag_damage_type": "wheel"}, record, at, "booking")
    assert (
        with_rec["bag"]["status"] == "damaged"
        and with_rec["bag"]["damage_type"] == "wheel"
        and with_rec["bag"]["checked"] is True
    )


def test_matched_record_is_proof_of_purchase():
    at = datetime(2026, 10, 1, 12, 0)
    record = {
        "order_id": "R-1",
        "placed_at": "2026-09-20T10:00:00Z",
        "total": "60.00",
        "items": [{"item_id": "L1", "sku": "C1", "name": "desk", "line_total": "60.00"}],
        "shipments": [],
    }
    f = build_facts({"reason": "change_of_mind", "has_receipt": False}, None, record, at, "order")
    assert f["claim"]["has_receipt"] is True
    assert (
        build_facts({"reason": "change_of_mind", "has_receipt": False}, None, None, at, "order")["claim"]["has_receipt"]
        is False
    )


def test_delay_cause_only_from_the_customers_words():
    from umpyr_engine.resolve.facts import grounded_cause

    assert grounded_cause("controllable", "they said it was a crew issue") == "controllable"
    assert grounded_cause("uncontrollable", "there was a snow storm in NY") == "uncontrollable"
    assert grounded_cause("uncontrollable", "kept delaying it, no eta, what are my options") is None
    assert grounded_cause("unknown", "weather") is None
