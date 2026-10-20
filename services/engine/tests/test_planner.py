"""Planner: step normalisation, consensus, agreement, comparison, structured case text. No model, no database."""

from __future__ import annotations

from umpyr_engine.resolve.planner import (
    agreement,
    compare,
    consensus,
    intents_from_claim,
    normalise_steps,
    structured_case_text,
)


def test_normalise_collapses_types_and_keeps_remedies():
    seq = [
        "acknowledge",
        "verify_order",
        "check_policy",
        "request_photos",
        "review_photos",
        "issue_refund",
        "close_ticket",
    ]
    assert normalise_steps(seq) == ["verify", "evidence", "issue_refund"]
    assert normalise_steps(["other", "acknowledge"]) == []
    assert normalise_steps(["verify_booking", "check_fare_rules", "issue_travel_credit"]) == [
        "verify",
        "issue_travel_credit",
    ]


def test_consensus_orders_by_median_position_and_filters_support():
    seqs = [
        ["verify", "evidence", "issue_replacement"],
        ["verify", "issue_replacement"],
        ["verify", "evidence", "issue_replacement"],
        ["evidence", "verify", "issue_refund"],
    ]
    steps, support = consensus(seqs)
    assert steps == ["verify", "evidence", "issue_replacement"]
    assert support["issue_refund"] == 0.25 and support["verify"] == 1.0
    assert 0 < agreement(seqs, steps) < 1
    assert consensus([]) == ([], {}) and agreement([], []) == 0.0


def test_compare_by_type_and_order_not_count():
    assert compare(["verify", "issue_refund"], ["verify", "issue_refund"])["kind"] == "agree"
    assert (
        compare(["verify", "evidence", "issue_refund"], ["evidence", "verify", "issue_refund"])["kind"]
        == "order_differs"
    )
    c = compare(["verify", "issue_store_credit"], ["verify", "issue_refund"])
    assert (
        c["kind"] == "diverge"
        and c["missing_in_live"] == ["issue_store_credit"]
        and c["extra_in_live"] == ["issue_refund"]
    )
    assert compare(["verify", "evidence", "issue_refund"], ["verify", "issue_refund"])["kind"] == "minor_diff"
    assert (
        compare(["verify", "issue_refund"], ["verify", "evidence", "issue_refund", "deny_request"])["kind"] == "diverge"
    )
    assert compare([], ["verify"])["kind"] == "no_history"
    assert compare(["verify"], [])["kind"] == "no_live"
    assert compare([], [])["kind"] == "none"


def test_structured_case_text_is_fixed_order_and_skips_unknowns():
    t = structured_case_text(
        "airline_delay",
        ["refund", "timeline"],
        {"reason": "flight_delayed", "requested_remedy": "refund", "has_photos": None, "timeline_ask": True},
        {"flight": {"delay_minutes": 180, "cause": "controllable"}, "booking": {"customer_cancelled": True}},
        {"pax_count": 3, "pax_types": ["adult", "adult", "child"]},
    )
    assert t == (
        "category: airline_delay | intents: refund,timeline | reason: flight_delayed | requested: refund | "
        "delay_minutes: 180 | cause: controllable | customer_cancelled: True | pax: 3 adult,adult,child | asks: timeline"
    )
    assert (
        structured_case_text(None, [], {}, {}, None)
        == "category: unknown | intents: unknown | reason: unknown | requested: unknown"
    )


def test_intents_from_claim_for_history():
    assert intents_from_claim("flight_delayed", "refund") == ["refund", "compensation"]
    assert intents_from_claim("damaged_on_arrival", None) == ["replacement"]
    assert intents_from_claim("change_of_mind", "store_credit") == ["refund", "return"]
    assert intents_from_claim(None, None) == ["other"]
