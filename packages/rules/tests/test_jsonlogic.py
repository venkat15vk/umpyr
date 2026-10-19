from decimal import Decimal

import pytest
from umpyr_rules import JsonLogicError, apply, collect_vars

DATA = {
    "order": {"total": Decimal("100.50"), "items": [{"price": 10, "cat": "a"}, {"price": 20, "cat": "b"}]},
    "flag": True,
}


def test_var_and_default():
    assert apply({"var": "order.total"}, DATA) == Decimal("100.50")
    assert apply({"var": "order.items.1.price"}, DATA) == 20
    assert apply({"var": "nope"}, DATA) is None
    assert apply({"var": ["nope", 7]}, DATA) == 7
    assert apply({"var": ""}, 5) == 5


def test_missing_and_missing_some():
    assert apply({"missing": ["order.total", "order.x", "y"]}, DATA) == ["order.x", "y"]
    assert apply({"missing_some": [1, ["order.total", "y"]]}, DATA) == []
    assert apply({"missing_some": [2, ["order.total", "y"]]}, DATA) == ["y"]


def test_comparisons_chain_and_none():
    assert apply({"<": [1, {"var": "order.items.0.price"}, 15]}, DATA) is True
    assert apply({"<": [1, {"var": "order.items.0.price"}, 5]}, DATA) is False
    assert apply({">=": [{"var": "order.total"}, 100.5]}, DATA) is True
    assert apply({">": [{"var": "nope"}, 1]}, DATA) is False
    assert apply({"<": ["a", "b"]}, DATA) is True


def test_decimal_arithmetic_is_exact():
    assert apply({"*": [{"var": "order.total"}, 0.8]}, DATA) == Decimal("80.40")
    assert apply({"+": [0.1, 0.2]}, {}) == Decimal("0.3")
    assert apply({"-": [5]}, {}) == Decimal("-5")
    assert apply({"/": [1, 4]}, {}) == Decimal("0.25")
    assert apply({"%": [7, 3]}, {}) == Decimal(1)
    assert apply({"min": [3, 1, 2]}, {}) == 1
    assert apply({"max": [3, 1, 2]}, {}) == 3
    with pytest.raises(JsonLogicError):
        apply({"/": [1, 0]}, {})


def test_equality_loose_and_strict():
    assert apply({"==": [1, "1"]}, {}) is True
    assert apply({"===": [1, "1"]}, {}) is False
    assert apply({"==": [True, 1]}, {}) is True
    assert apply({"!=": [None, 0]}, {}) is True
    assert apply({"==": [None, None]}, {}) is True
    assert apply({"!": [0]}, {}) is True
    assert apply({"!!": ["x"]}, {}) is True


def test_and_or_short_circuit_and_if():
    assert apply({"and": [True, {"var": "order.total"}]}, DATA) == Decimal("100.50")
    assert apply({"and": [False, {"/": [1, 0]}]}, DATA) is False  # not evaluated
    assert apply({"or": [False, "x"]}, {}) == "x"
    assert apply({"if": [False, 1, True, 2, 3]}, {}) == 2
    assert apply({"if": [False, 1, 3]}, {}) == 3
    assert apply({"if": [False, 1]}, {}) is None


def test_in_cat_merge_substr():
    assert apply({"in": ["a", ["a", "b"]]}, {}) is True
    assert apply({"in": ["z", ["a", "b"]]}, {}) is False
    assert apply({"in": ["ell", "hello"]}, {}) is True
    assert apply({"in": ["a", None]}, {}) is False
    assert apply({"cat": ["a", 1, None]}, {}) == "a1"
    assert apply({"merge": [[1], 2, [3, 4]]}, {}) == [1, 2, 3, 4]
    assert apply({"substr": ["hello", 1, 3]}, {}) == "ell"


def test_collection_ops():
    assert apply({"some": [{"var": "order.items"}, {">": [{"var": "price"}, 15]}]}, DATA) is True
    assert apply({"all": [{"var": "order.items"}, {">": [{"var": "price"}, 15]}]}, DATA) is False
    assert apply({"none": [{"var": "order.items"}, {">": [{"var": "price"}, 50]}]}, DATA) is True
    assert apply({"all": [{"var": "missing_list"}, True]}, DATA) is False
    assert apply({"map": [{"var": "order.items"}, {"var": "price"}]}, DATA) == [10, 20]
    assert apply({"filter": [{"var": "order.items"}, {"==": [{"var": "cat"}, "b"]}]}, DATA) == [
        {"price": 20, "cat": "b"}
    ]
    assert apply(
        {"reduce": [{"var": "order.items"}, {"+": [{"var": "accumulator"}, {"var": "current.price"}]}, 0]}, DATA
    ) == Decimal(30)


def test_unknown_operator_raises():
    with pytest.raises(JsonLogicError):
        apply({"explode": [1]}, {})


def test_collect_vars():
    logic = {
        "and": [
            {"==": [{"var": "item.is_final_sale"}, True]},
            {"some": [{"var": "order.items"}, {">": [{"var": "price"}, 1]}]},
            {"missing": ["claim.has_photos"]},
        ]
    }
    assert collect_vars(logic) == {"item.is_final_sale", "order.items", "price", "claim.has_photos"}


def test_determinism():
    logic = {"if": [{">": [{"var": "order.total"}, 50]}, {"*": [{"var": "order.total"}, 0.5]}, 0]}
    results = {str(apply(logic, DATA)) for _ in range(50)}
    assert results == {"50.250"}
