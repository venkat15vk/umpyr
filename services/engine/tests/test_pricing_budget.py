from decimal import Decimal

import pytest
from umpyr_engine.llm.base import Usage
from umpyr_engine.llm.budget import BudgetExceeded
from umpyr_engine.llm.pricing import cost_usd, price_for


def test_price_lookup_prefix_and_fallback():
    assert price_for("claude-haiku-5-5").input == Decimal("0.10")
    assert price_for("claude-haiku-5-5-20260101").input == Decimal("0.10")
    assert price_for("claude-haiku-4-5").input == Decimal("1.00")
    assert price_for("claude-mystery").model_prefix == "claude-haiku-4-5"  # never under-count
    assert price_for("voyage-3.5-lite").input == Decimal("0.02")
    assert price_for("voyage-3.5").input == Decimal("0.06")


def test_cost_components_and_batch():
    # 10K input, 10K output, 10K cache write, 10K cache read on Haiku 5.5 (short-prompt card)
    c = cost_usd("claude-haiku-5-5", 10_000, 10_000, 10_000, 10_000)
    assert c == Decimal("0.001") + Decimal("0.005") + Decimal("0.00125") + Decimal("0.0001")
    assert cost_usd("claude-haiku-5-5", 10_000, 10_000, 10_000, 10_000, is_batch=True) == c / 2
    assert cost_usd("claude-haiku-5-5", 1000, 200) == Decimal("0.000200")


def test_long_prompt_rate_card():
    # 150K prompt tokens on Haiku 5.5 -> $0.50/M input
    assert cost_usd("claude-haiku-5-5", 150_000, 0) == Decimal("0.075000")
    assert cost_usd("claude-haiku-5-5", 100_000, 0) == Decimal("0.010000")


def test_budget_records_and_blocks(budget):
    assert budget.spent_today() == Decimal(0)
    budget.check()
    # 100K output tokens = $0.05 on Haiku 5.5 -> reaches the test cap exactly
    cost = budget.record(
        model="claude-haiku-5-5",
        step="mine",
        workspace_id="rockler",
        usage=Usage(input_tokens=0, output_tokens=100_000),
        is_batch=False,
        request_id="req_1",
        latency_ms=10,
    )
    assert cost == Decimal("0.05")
    with pytest.raises(BudgetExceeded) as e:
        budget.check()
    assert "Demo budget reached for today" in str(e.value)
    s = budget.summary()
    assert s["blocked"] is True and s["calls"] == 1 and s["spent_usd"] == pytest.approx(0.05)
    assert s["by_step"][0]["step"] == "mine" and s["by_step"][0]["output_tokens"] == 100_000


def test_budget_accumulates_across_calls(budget):
    for _ in range(3):
        budget.record(
            model="claude-haiku-5-5",
            step="classify",
            workspace_id=None,
            usage=Usage(input_tokens=10_000, output_tokens=0),
            is_batch=True,
            request_id=None,
            latency_ms=None,
        )
    assert budget.spent_today() == Decimal("0.0015")
    assert budget.summary()["calls"] == 3
