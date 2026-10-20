"""Customer experience helpers: next-question classification, cited refund timeline, options, choice, queue. No model."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from umpyr_engine.resolve.customer import (
    apply_choice,
    classify_question,
    detect_choice,
    is_overdue,
    public_citations,
    queue_state,
    refund_options,
    sla_sentence,
    status_lines,
    timeline_answer,
)
from umpyr_rules import Check, Clause, Effect, Provenance, Rule, RulePack, evaluate


def _pack() -> RulePack:
    prov = lambda doc, public=True: Provenance(
        document_id=doc, document_title=doc, document_version="a" * 64, text_hash="b" * 64, public=public
    )  # noqa: E731
    return RulePack(
        workspace="t",
        version="t1",
        clauses=[
            Clause(
                clause_id="L-REF-1",
                title="DOT refund",
                text="Refunds to a credit card must be issued within seven business days of the request; refunds by cash or check within twenty days.",
                source_url="https://law/260",
                tier="law",
                provenance=prov("US DOT 14 CFR 260"),
            ),
            Clause(
                clause_id="C-CRED-1",
                title="Credit",
                text="A travel credit may be offered instead of a refund.",
                source_url="https://coc",
                tier="commitment",
                provenance=prov("Customer Commitment"),
            ),
            Clause(
                clause_id="P-1",
                title="Practice",
                text="refund within 3 days",
                source_url="practice://t/1",
                tier="goodwill",
                provenance=prov("practice", public=False),
            ),
        ],
        rules=[
            Rule(
                rule_id="L-REF-1.r1",
                clause_id="L-REF-1",
                tier="law",
                claim_reasons=["flight_delayed"],
                requires=["flight.delay_minutes", "booking.total_paid"],
                when={">=": [{"var": "flight.delay_minutes"}, 180]},
                effect=Effect(decision="approve", remedy="refund", amount={"var": "booking.total_paid"}),
            ),
            Rule(
                rule_id="C-CRED-1.r1",
                clause_id="C-CRED-1",
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


FACTS = {"claim": {"reason": "flight_delayed"}, "flight": {"delay_minutes": 200}, "booking": {"total_paid": "500"}}


def test_classify_question():
    assert classify_question("When will the refund land on my card?") == ["timeline"]
    assert "how_to_return" in classify_question("Do you send a label so I can ship it back?")
    assert "escalate" in classify_question("This is unacceptable, I want a manager")
    assert classify_question("Thanks, that works for me.") == []


def test_timeline_answer_is_cited_public_and_payment_aware():
    pack = _pack()
    card = timeline_answer(pack, "card")
    assert card and card["days"] == 7 and card["day_kind"] == "business" and card["citation"]["clause_id"] == "L-REF-1"
    assert "7 business days" in card["answer"] and "US DOT 14 CFR 260" in card["answer"]
    cash = timeline_answer(pack, "cash")
    assert cash and cash["days"] == 20
    assert timeline_answer(None, "card") is None
    # a private practice clause is never cited even if it is the shortest window
    assert all(timeline_answer(pack, pm)["citation"]["clause_id"] != "P-1" for pm in ("card", "cash"))


def test_refund_options_cash_first_and_choice_rebinds():
    pack = _pack()
    ruling = evaluate(pack, FACTS)
    assert ruling.remedy == "refund" and ruling.amount == Decimal("500")
    opts = refund_options(ruling, pack)
    assert [o["remedy"] for o in opts] == ["refund", "travel_credit"]
    assert (
        opts[0]["guaranteed_by_regulation"]
        and Decimal(opts[0]["amount"]) == Decimal("500")
        and Decimal(opts[1]["amount"]) == Decimal("550")
    )
    assert opts[1]["citation"]["document"] == "Customer Commitment"
    chosen = apply_choice(ruling, opts, "travel_credit", FACTS)
    assert (
        chosen.remedy == "travel_credit"
        and chosen.amount == Decimal("550.0")
        and chosen.winning_rule_id == "C-CRED-1.r1"
    )
    assert str(chosen.decision) == "approve" and "chose travel credit" in chosen.explanation
    assert apply_choice(ruling, opts, "refund", FACTS) is ruling
    assert refund_options(None, pack) == []
    # one remedy only -> no menu
    one = RulePack(workspace="t", version="t2", clauses=pack.clauses, rules=pack.rules[:1])
    assert refund_options(evaluate(one, FACTS), one) == []


def test_detect_choice():
    opts = refund_options(evaluate(_pack(), FACTS), _pack())
    assert detect_choice(opts, {"claim": {"requested_remedy": "travel_credit"}}, "") == "travel_credit"
    assert detect_choice(opts, {"claim": {"wants_credit_instead_of_refund": True}}, "") == "travel_credit"
    assert detect_choice(opts, {"claim": {}}, "I'll take the travel credit, thanks") == "travel_credit"
    assert detect_choice(opts, {"claim": {}}, "please refund my card") == "refund"
    assert detect_choice(opts, {"claim": {}}, "ok thanks") is None
    assert detect_choice([], {"claim": {"requested_remedy": "refund"}}, "refund") is None


def test_queue_state_and_overdue():
    now = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    q = queue_state(None, 4, now)
    assert q["sla_hours"] == 4 and q["due_at"] == (now + timedelta(hours=4)).isoformat() and q["overdue"] is False
    later = queue_state(q, 4, now + timedelta(hours=5))
    assert later["queued_at"] == q["queued_at"] and later["overdue"] is True
    assert is_overdue(q, now + timedelta(hours=3)) is False and is_overdue(q, now + timedelta(hours=5)) is True
    assert (
        queue_state(None, None, now)["due_at"] is None
        and is_overdue(queue_state(None, None, now), now + timedelta(days=9)) is False
    )
    assert (
        sla_sentence(4) == "within 4 hours" and sla_sentence(0.5) == "within 30 minutes" and sla_sentence(None) is None
    )


def test_status_lines_and_public_citations():
    lines = status_lines(
        [
            {"action": "message.received"},
            {"action": "record.lookup"},
            {"action": "rule_check"},
            {"action": "gate.queued"},
            {"action": "rule_check"},
        ],
        "booking",
    )
    assert lines == [
        "Received your message",
        "Checked your booking",
        "Reviewed what the policy provides",
        "Sent for authorisation",
    ]
    cits = public_citations(
        [
            {
                "clause_id": "A",
                "document": "CoC",
                "section": "Rule 17",
                "page": 88,
                "url": "u",
                "document_version": "v",
                "public": True,
                "quote": "x" * 300,
            },
            {"clause_id": "P", "document": "practice", "public": False, "quote": "y"},
        ]
    )
    assert (
        len(cits) == 1 and cits[0]["document"] == "CoC" and "clause_id" not in cits[0] and len(cits[0]["quote"]) == 200
    )


def test_check_shape_used_by_options():
    c = Check(
        rule_id="r",
        clause_id="c",
        tier="law",
        priority=0,
        applicable=True,
        fired=True,
        effect=Effect(decision="approve", remedy="refund"),
        amount=Decimal("1"),
    )
    assert c.effect.remedy == "refund"


def test_small_talk_is_only_pleasantries():
    from umpyr_engine.resolve.customer import is_small_talk

    assert is_small_talk("Good, i like you")
    assert is_small_talk("thanks so much!")
    assert is_small_talk("ok cool")
    assert not is_small_talk("ok, i actually found the bill now")
    assert not is_small_talk("thanks, when will I get it?")
    assert not is_small_talk("good, and the refund?")
