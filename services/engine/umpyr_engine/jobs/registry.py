"""Job handlers by kind. Later milestones register onboarding steps here."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .queue import JobContext

Handler = Callable[[JobContext], dict[str, Any] | None]
HANDLERS: dict[str, Handler] = {}


def register(kind: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return deco


@register("demo")
def demo_job(ctx: JobContext) -> dict[str, Any]:
    """Smoke-test job: emits three events with progress."""
    steps = ["ingest", "mine", "taxonomy"]
    delay = float(ctx.params.get("delay", 0.2))
    for i, step in enumerate(steps, start=1):
        ctx.emit(step, f"demo step {step}", progress=int(i * 100 / len(steps)))
        time.sleep(delay)
    return {"steps": steps}
