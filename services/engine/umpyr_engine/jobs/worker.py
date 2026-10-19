"""Background worker: polls the jobs table and runs handlers."""

from __future__ import annotations

import logging
import socket
import time
import traceback

from ..config import get_settings
from ..db.base import get_session_factory
from ..llm.budget import BudgetExceeded
from . import registry  # noqa: F401  (registers handlers)
from .queue import JobContext, claim, finish
from .registry import HANDLERS

log = logging.getLogger(__name__)


def run_one(worker_id: str) -> bool:
    factory = get_session_factory()
    with factory() as session:
        job = claim(session, worker_id)
        if job is None:
            return False
        ctx = JobContext(session, job)
        handler = HANDLERS.get(job.kind)
        if handler is None:
            finish(session, job, error=f"no handler for job kind '{job.kind}'")
            return True
        try:
            ctx.emit("start", f"job {job.kind} started", progress=0)
            result = handler(ctx)
            ctx.emit("done", f"job {job.kind} finished", progress=100)
            finish(session, job, result=result)
        except BudgetExceeded as e:
            ctx.emit("budget", str(e), level="error")
            finish(session, job, error="budget_exceeded")
        except Exception as e:  # noqa: BLE001
            log.error("job %s failed: %s\n%s", job.id, e, traceback.format_exc())
            ctx.emit("error", f"{type(e).__name__}: {e}", level="error")
            finish(session, job, error=f"{type(e).__name__}: {e}")
        return True


def main_loop() -> None:
    settings = get_settings()
    worker_id = f"{socket.gethostname()}-{int(time.time())}"
    log.info("worker %s started (provider=%s model=%s)", worker_id, settings.llm_provider, settings.claude_model)
    while True:
        try:
            if not run_one(worker_id):
                time.sleep(settings.worker_poll_seconds)
        except KeyboardInterrupt:
            break
        except Exception as e:  # noqa: BLE001
            log.error("worker loop error: %s", e)
            time.sleep(2)
