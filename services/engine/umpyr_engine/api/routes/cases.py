"""Cases: the case object, its hash-chained ledger, chain verification, and workspace metrics (design v2 §3, §15, §16)."""

from __future__ import annotations

import uuid
from collections import Counter
from decimal import Decimal
from statistics import mean, median

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import models as m
from ...resolve.ledger import CaseLedger
from ..deps import db_session

router = APIRouter(tags=["cases"])


def _case_dict(c: m.ResolveCase) -> dict:
    return {
        "id": str(c.id),
        "workspace_id": c.workspace_id,
        "session_id": str(c.session_id),
        "status": c.status,
        "category_key": c.category_key,
        "reference": c.reference,
        "escalation": c.escalation,
        "turns": c.turns,
        "cost_usd": str(c.cost_usd),
        "llm_calls": c.llm_calls,
        "event_count": c.event_count,
        "head_hash": c.head_hash,
        "decision_hash": c.decision_hash,
        "executed": (c.state or {}).get("executed", []),
        "opened_at": c.created_at.isoformat() if c.created_at else None,
        "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
    }


def _event_dict(e: m.CaseEvent) -> dict:
    return {
        "seq": e.seq,
        "turn": e.turn,
        "actor": e.actor,
        "action": e.action,
        "basis": e.basis,
        "result": e.result,
        "cost_usd": str(e.cost_usd),
        "at": e.at.isoformat() if e.at else None,
        "prev_hash": e.prev_hash,
        "hash": e.hash,
    }


def _get_case(session: Session, case_id: uuid.UUID) -> m.ResolveCase:
    c = session.get(m.ResolveCase, case_id)
    if c is None:
        raise HTTPException(404, "case not found")
    return c


@router.get("/sessions/{session_id}/case")
def session_case(session_id: uuid.UUID, session: Session = Depends(db_session)) -> dict:
    c = session.execute(select(m.ResolveCase).where(m.ResolveCase.session_id == session_id)).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, "no case for this session yet")
    return _case_dict(c)


@router.get("/cases/{case_id}")
def get_case(case_id: uuid.UUID, session: Session = Depends(db_session)) -> dict:
    return _case_dict(_get_case(session, case_id))


@router.get("/cases/{case_id}/events")
def case_events(case_id: uuid.UUID, limit: int = 500, session: Session = Depends(db_session)) -> list[dict]:
    _get_case(session, case_id)
    rows = session.execute(
        select(m.CaseEvent).where(m.CaseEvent.case_id == case_id).order_by(m.CaseEvent.seq).limit(limit)
    ).scalars()
    return [_event_dict(e) for e in rows]


@router.get("/cases/{case_id}/verify")
def verify_case(case_id: uuid.UUID, session: Session = Depends(db_session)) -> dict:
    _get_case(session, case_id)
    return CaseLedger.verify(session, case_id)


@router.get("/workspaces/{workspace_id}/metrics")
def metrics(workspace_id: str, session: Session = Depends(db_session)) -> dict:
    """Learning-loop metrics that phase 1 can compute: cases by status, escalations by type, cost per case,
    cost per resolved case, time to resolution. Plan accuracy, refund error and chargeback rates arrive with
    later phases."""
    if session.get(m.Workspace, workspace_id) is None:
        raise HTTPException(404, "workspace not found")
    cases = session.execute(select(m.ResolveCase).where(m.ResolveCase.workspace_id == workspace_id)).scalars().all()
    by_status = Counter(c.status for c in cases)
    esc = [c for c in cases if c.escalation]
    by_type = Counter((c.escalation or {}).get("type", "unknown") for c in esc)
    resolved = [c for c in cases if c.status in ("resolved", "denied")]
    total_cost = sum((Decimal(c.cost_usd or 0) for c in cases), Decimal(0))
    resolved_cost = sum((Decimal(c.cost_usd or 0) for c in resolved), Decimal(0))
    ttr = [
        (c.resolved_at - c.created_at).total_seconds() / 3600
        for c in resolved
        if c.resolved_at is not None and c.created_at is not None
    ]
    n = len(cases)
    return {
        "workspace_id": workspace_id,
        "cases": {"total": n, "by_status": dict(by_status), "resolved": len(resolved)},
        "escalations": {
            "count": len(esc),
            "rate": round(len(esc) / n, 3) if n else None,
            "by_type": dict(by_type),
        },
        "cost": {
            "total_usd": str(total_cost),
            "per_case_usd": str((total_cost / n).quantize(Decimal("0.000001"))) if n else None,
            "per_resolved_case_usd": str((resolved_cost / len(resolved)).quantize(Decimal("0.000001")))
            if resolved
            else None,
            "llm_calls": sum(int(c.llm_calls or 0) for c in cases),
        },
        "time_to_resolution_hours": {
            "median": round(median(ttr), 4) if ttr else None,
            "mean": round(mean(ttr), 4) if ttr else None,
            "n": len(ttr),
        },
        "turns_per_case": round(mean(int(c.turns or 0) for c in cases), 2) if cases else None,
        "not_yet": ["plan_accuracy", "refund_error_rate", "chargeback_after_resolution_rate"],
    }
