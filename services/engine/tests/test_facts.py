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
