"""Budget guard: records every LLM call and blocks calls once today's UTC spend reaches the cap.

Totals live in Postgres (`daily_spend`, keyed by UTC day) so the API and the
worker share one counter. Every call is also logged in `llm_calls` with its
pipeline step for the admin endpoint.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from ..db.models import DailySpend, LLMCall
from .base import Usage
from .pricing import cost_usd

log = logging.getLogger(__name__)


class BudgetExceeded(RuntimeError):
    def __init__(self, spent: Decimal, cap: Decimal):
        super().__init__(f"Demo budget reached for today: spent ${spent:.4f} of ${cap:.2f}")
        self.spent = spent
        self.cap = cap


def utc_today():
    return datetime.now(UTC).date()


class BudgetGuard:
    def __init__(self, session_factory: sessionmaker[Session], cap_usd: Decimal):
        self.session_factory = session_factory
        self.cap = Decimal(cap_usd)

    def spent_today(self) -> Decimal:
        with self.session_factory() as s:
            row = s.get(DailySpend, utc_today())
            return Decimal(row.cost_usd) if row else Decimal(0)

    def check(self) -> None:
        spent = self.spent_today()
        if spent >= self.cap:
            raise BudgetExceeded(spent, self.cap)

    def record(
        self,
        *,
        model: str,
        step: str,
        workspace_id: str | None,
        usage: Usage,
        is_batch: bool,
        request_id: str | None,
        latency_ms: int | None,
    ) -> Decimal:
        cost = cost_usd(
            model,
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_write_tokens,
            usage.cache_read_tokens,
            is_batch=is_batch,
        )
        day = utc_today()
        with self.session_factory() as s:
            s.add(
                LLMCall(
                    workspace_id=workspace_id,
                    step=step,
                    model=model,
                    provider="claude",
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_write_tokens=usage.cache_write_tokens,
                    cache_read_tokens=usage.cache_read_tokens,
                    cost_usd=cost,
                    is_batch=is_batch,
                    request_id=request_id,
                    latency_ms=latency_ms,
                    day=day,
                )
            )
            stmt = pg_insert(DailySpend).values(day=day, cost_usd=cost, calls=1)
            stmt = stmt.on_conflict_do_update(
                index_elements=[DailySpend.day],
                set_={
                    "cost_usd": DailySpend.cost_usd + stmt.excluded.cost_usd,
                    "calls": DailySpend.calls + 1,
                    "updated_at": func.now(),
                },
            )
            s.execute(stmt)
            s.commit()
        log.info("llm call step=%s tokens in=%d out=%d cost=$%.6f", step, usage.input_tokens, usage.output_tokens, cost)
        return cost

    def summary(self) -> dict:
        day = utc_today()
        with self.session_factory() as s:
            total = s.get(DailySpend, day)
            by_step = s.execute(
                select(
                    LLMCall.step,
                    func.count().label("calls"),
                    func.sum(LLMCall.input_tokens).label("input_tokens"),
                    func.sum(LLMCall.output_tokens).label("output_tokens"),
                    func.sum(LLMCall.cache_read_tokens).label("cache_read_tokens"),
                    func.sum(LLMCall.cache_write_tokens).label("cache_write_tokens"),
                    func.sum(LLMCall.cost_usd).label("cost_usd"),
                )
                .where(LLMCall.day == day)
                .group_by(LLMCall.step)
                .order_by(func.sum(LLMCall.cost_usd).desc())
            ).all()
            all_time = s.execute(
                select(func.coalesce(func.sum(LLMCall.cost_usd), 0), func.count()).select_from(LLMCall)
            ).one()
        spent = Decimal(total.cost_usd) if total else Decimal(0)
        return {
            "day": day.isoformat(),
            "cap_usd": float(self.cap),
            "spent_usd": float(spent),
            "remaining_usd": float(max(self.cap - spent, Decimal(0))),
            "blocked": spent >= self.cap,
            "calls": total.calls if total else 0,
            "by_step": [
                {
                    "step": r.step,
                    "calls": r.calls,
                    "input_tokens": int(r.input_tokens or 0),
                    "output_tokens": int(r.output_tokens or 0),
                    "cache_read_tokens": int(r.cache_read_tokens or 0),
                    "cache_write_tokens": int(r.cache_write_tokens or 0),
                    "cost_usd": float(r.cost_usd or 0),
                }
                for r in by_step
            ],
            "all_time_usd": float(all_time[0] or 0),
            "all_time_calls": int(all_time[1] or 0),
        }
