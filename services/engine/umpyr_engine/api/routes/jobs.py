from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from ...db.base import get_session_factory
from ...db.models import Job
from ...jobs.queue import TERMINAL, enqueue, events_after
from ...jobs.registry import HANDLERS
from ..deps import db_session

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _job_dict(job: Job) -> dict:
    return {
        "id": str(job.id),
        "kind": job.kind,
        "workspace_id": job.workspace_id,
        "status": job.status,
        "progress": job.progress,
        "params": job.params,
        "result": job.result,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


@router.post("/{kind}")
def create_job(
    kind: str, params: dict | None = None, workspace_id: str | None = None, session: Session = Depends(db_session)
) -> dict:
    if kind not in HANDLERS:
        raise HTTPException(404, f"unknown job kind '{kind}'")
    job = enqueue(session, kind, params or {}, workspace_id)
    return _job_dict(job)


@router.get("/{job_id}")
def get_job(job_id: uuid.UUID, session: Session = Depends(db_session)) -> dict:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return _job_dict(job)


@router.get("/{job_id}/events")
async def stream_events(job_id: uuid.UUID, after: int = 0):
    """Server-sent events: one `event: step` per job event, then `event: end` when the job is terminal."""
    factory = get_session_factory()

    def _load(after_seq: int):
        with factory() as s:
            job = s.get(Job, job_id)
            if job is None:
                return None, []
            return job, events_after(s, job_id, after_seq)

    job, _ = await asyncio.to_thread(_load, after)
    if job is None:
        raise HTTPException(404, "job not found")

    async def gen():
        last = after
        while True:
            job, events = await asyncio.to_thread(_load, last)
            for ev in events:
                last = ev.seq
                yield {
                    "event": "step",
                    "id": str(ev.seq),
                    "data": json.dumps(
                        {
                            "seq": ev.seq,
                            "step": ev.step,
                            "level": ev.level,
                            "message": ev.message,
                            "data": ev.data,
                            "progress": ev.progress,
                            "at": ev.created_at.isoformat() if ev.created_at else None,
                        }
                    ),
                }
            if job is None or job.status in TERMINAL:
                yield {"event": "end", "data": json.dumps(_job_dict(job) if job else {})}
                return
            await asyncio.sleep(0.5)

    return EventSourceResponse(gen())
