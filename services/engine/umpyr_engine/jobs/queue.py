"""Postgres-backed job queue (no Redis). Workers claim with FOR UPDATE SKIP LOCKED."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..db.models import Job, JobEvent

log = logging.getLogger(__name__)

TERMINAL = ("done", "failed")


def enqueue(session: Session, kind: str, params: dict[str, Any] | None = None, workspace_id: str | None = None) -> Job:
    job = Job(kind=kind, params=params or {}, workspace_id=workspace_id, status="queued")
    session.add(job)
    session.commit()
    return job


def claim(session: Session, worker_id: str) -> Job | None:
    row = session.execute(
        text(
            """
            UPDATE jobs SET status = 'running', locked_by = :worker, started_at = now(), attempts = attempts + 1
            WHERE id = (
                SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at
                FOR UPDATE SKIP LOCKED LIMIT 1
            )
            RETURNING id
            """
        ),
        {"worker": worker_id},
    ).first()
    session.commit()
    if row is None:
        return None
    return session.get(Job, row[0])


def finish(session: Session, job: Job, result: dict[str, Any] | None = None, error: str | None = None) -> None:
    job.status = "failed" if error else "done"
    job.error = error
    job.result = result
    job.progress = 100 if not error else job.progress
    job.finished_at = datetime.now(UTC)
    session.add(job)
    session.commit()


class JobContext:
    """Handed to job handlers: emit progress events and get sessions."""

    def __init__(self, session: Session, job: Job):
        self.session = session
        self.job = job
        self._seq = session.execute(
            select(func.coalesce(func.max(JobEvent.seq), 0)).where(JobEvent.job_id == job.id)
        ).scalar_one()

    @property
    def workspace_id(self) -> str | None:
        return self.job.workspace_id

    @property
    def params(self) -> dict[str, Any]:
        return self.job.params or {}

    def emit(
        self,
        step: str,
        message: str,
        *,
        level: str = "info",
        data: dict[str, Any] | None = None,
        progress: int | None = None,
    ) -> JobEvent:
        self._seq += 1
        ev = JobEvent(
            job_id=self.job.id,
            workspace_id=self.job.workspace_id,
            seq=self._seq,
            step=step,
            level=level,
            message=message,
            data=data or {},
            progress=progress,
        )
        self.session.add(ev)
        if progress is not None:
            self.job.progress = progress
            self.session.add(self.job)
        self.session.commit()
        log.info("job %s [%s] %s", str(self.job.id)[:8], step, message)
        return ev


def events_after(session: Session, job_id: uuid.UUID, after_seq: int) -> list[JobEvent]:
    return list(
        session.execute(
            select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.seq > after_seq).order_by(JobEvent.seq)
        ).scalars()
    )
