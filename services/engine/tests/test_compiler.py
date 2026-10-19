from umpyr_engine.compile.compiler import _build_rule, apply_overrides, chunk_text, find_conflicts, lint_rule
from umpyr_rules import Effect, Rule

CLAUSE = {"clause_id": "X-RET-1", "tier": "contract"}


def test_chunk_text_respects_paragraphs_and_size():
    text = "\n\n".join(f"para {i} " + "x" * 400 for i in range(60))
    chunks = chunk_text(text, size=5000)
    assert len(chunks) > 3 and all(len(c) <= 7500 for c in chunks)
    assert "".join(chunks).count("para ") == 60


def test_build_rule_parses_json_logic_strings_and_validates_paths():
    r = _build_rule(
        CLAUSE,
        {
            "description": "d",
            "claim_reasons": ["change_of_mind"],
            "requires": ["order.days_since_order"],
            "when": '{"<=": [{"var": "order.days_since_order"}, 90]}',
            "decision": "approve",
            "remedy": "refund",
            "amount": '{"var": "item.line_total"}',
            "fault": None,
            "note": "",
            "priority": 0,
            "confidence": 0.9,
        },
        1,
    )
    assert (
        r.rule_id == "X-RET-1.r1"
        and r.when == {"<=": [{"var": "order.days_since_order"}, 90]}
        and r.effect.amount == {"var": "item.line_total"}
    )
    try:
        _build_rule(
            CLAUSE,
            {
                "description": "d",
                "claim_reasons": [],
                "requires": [],
                "when": '{"==": [{"var": "zendesk.tag"}, 1]}',
                "decision": "deny",
                "remedy": None,
                "amount": None,
                "fault": None,
                "note": "",
                "priority": 0,
                "confidence": 0.5,
            },
            2,
        )
    except ValueError as e:
        assert "unknown ontology path" in str(e)
    else:
        raise AssertionError("expected validation error")


def rule(rid, when, decision, remedy=None, prio=0, reasons=None, requires=None):
    return Rule(
        rule_id=rid,
        clause_id="X-RET-1",
        tier="contract",
        priority=prio,
        claim_reasons=reasons or [],
        requires=requires or [],
        when=when,
        effect=Effect(decision=decision, remedy=remedy),
    )


def test_lint_rejects_unconditional_and_noop_rules():
    assert lint_rule(rule("a", True, "deny")) == "unconditional outcome (when=true)"
    assert (
        lint_rule(rule("b", {"==": [{"var": "item.is_final_sale"}, True]}, "approve", "none"))
        == "approve with no remedy"
    )
    assert lint_rule(rule("c", {"==": [{"var": "item.is_final_sale"}, True]}, "deny")) is None
    assert lint_rule(rule("d", True, "needs_approval", reasons=["other"])) == "no facts referenced"


def test_conflicts_require_overlap_in_reasons_and_fields():
    a = rule(
        "a",
        {"==": [{"var": "item.is_final_sale"}, True]},
        "deny",
        reasons=["change_of_mind"],
        requires=["item.is_final_sale"],
    )
    b = rule(
        "b",
        {"==": [{"var": "item.is_final_sale"}, False]},
        "approve",
        "refund",
        reasons=["change_of_mind"],
        requires=["item.is_final_sale"],
    )
    c = rule(
        "c", {"==": [{"var": "bag.checked"}, True]}, "approve", "refund", reasons=["bag_lost"], requires=["bag.checked"]
    )
    out = find_conflicts([a, b, c])
    assert len(out) == 1 and "a (deny/None) vs b (approve/refund)" in out[0]


def test_apply_overrides_disable_patch_add_enable_only():
    a = rule("a", {"==": [{"var": "item.is_final_sale"}, True]}, "deny")
    b = rule("b", {"==": [{"var": "item.is_power_tool"}, True]}, "needs_approval")
    rules, log = apply_overrides(
        [a, b],
        {
            "disable": ["b"],
            "patch": {"a": {"priority": 10, "effect": {"note": "final sale"}}},
            "add": [
                {
                    "rule_id": "x.m1",
                    "clause_id": "X-RET-1",
                    "tier": "contract",
                    "when": {"==": [{"var": "claim.has_photos"}, False]},
                    "effect": {"decision": "needs_fact"},
                }
            ],
        },
    )
    assert (
        [r.rule_id for r in rules] == ["a", "x.m1"] and rules[0].priority == 10 and rules[0].effect.note == "final sale"
    )
    assert len(log) == 3
    rules2, log2 = apply_overrides([a, b], {"enable_only": ["b"]})
    assert [r.rule_id for r in rules2] == ["b"] and log2[0].startswith("enable_only")
