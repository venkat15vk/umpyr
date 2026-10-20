"""Per-case cost meter.

The resolve runtime opens a meter for the turn (`metered(case_id, session_id)`); the LLM provider adds every
call it makes while the meter is active. The runtime drains the meter after each stage so the ledger event
for that stage carries exactly the calls and cost it caused. The provider also stamps `case_id`/`session_id`
on the `llm_calls` row, so cost per case is queryable without the meter.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

_current: ContextVar[CostMeter | None] = ContextVar("umpyr_cost_meter", default=None)


@dataclass
class CostMeter:
    case_id: UUID | None = None
    session_id: UUID | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    total_cost: Decimal = Decimal(0)
    total_calls: int = 0

    def add(self, step: str, cost: Decimal | float, input_tokens: int = 0, output_tokens: int = 0) -> None:
        c = Decimal(str(cost))
        self.calls.append({"step": step, "cost_usd": c, "input_tokens": input_tokens, "output_tokens": output_tokens})
        self.total_cost += c
        self.total_calls += 1

    def take(self) -> dict[str, Any]:
        """Calls since the last take (one pipeline stage)."""
        calls, self.calls = self.calls, []
        cost = sum((c["cost_usd"] for c in calls), Decimal(0))
        return {
            "calls": len(calls),
            "cost_usd": cost,
            "steps": sorted({c["step"] for c in calls}),
            "input_tokens": sum(c["input_tokens"] for c in calls),
            "output_tokens": sum(c["output_tokens"] for c in calls),
        }


def current() -> CostMeter | None:
    return _current.get()


@contextmanager
def metered(case_id: UUID | None = None, session_id: UUID | None = None):
    meter = CostMeter(case_id=case_id, session_id=session_id)
    token = _current.set(meter)
    try:
        yield meter
    finally:
        _current.reset(token)
