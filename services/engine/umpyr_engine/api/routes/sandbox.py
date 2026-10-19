"""Read-only views of onboarding results per workspace (consumed by the setup/ops UI)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...db import models as m
from ..deps import db_session

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["onboarding"])


def _ws(session: Session, workspace_id: str) -> m.Workspace:
    ws = session.get(m.Workspace, workspace_id)
    if ws is None:
        raise HTTPException(404, "workspace not found")
    return ws


@router.get("/summary")
def summary(workspace_id: str, session: Session = Depends(db_session)) -> dict:
    ws = _ws(session, workspace_id)

    def count(model):
        return session.execute(
            select(func.count()).select_from(model).where(model.workspace_id == workspace_id)
        ).scalar_one()

    pack = (
        session.execute(
            select(m.RulePack).where(m.RulePack.workspace_id == workspace_id, m.RulePack.status == "active")
        )
        .scalars()
        .first()
    )
    router_row = (
        session.execute(
            select(m.RouterModel).where(m.RouterModel.workspace_id == workspace_id, m.RouterModel.status == "active")
        )
        .scalars()
        .first()
    )
    drift = session.execute(
        select(m.DriftFinding.kind, func.count())
        .where(m.DriftFinding.workspace_id == workspace_id)
        .group_by(m.DriftFinding.kind)
    ).all()
    gold = session.execute(
        select(m.Case.split, func.count()).where(m.Case.workspace_id == workspace_id).group_by(m.Case.split)
    ).all()
    return {
        "workspace": {
            "id": ws.id,
            "name": ws.name,
            "kind": ws.kind,
            "status": ws.status,
            "is_synthetic": ws.is_synthetic,
            "settings": ws.settings,
        },
        "tickets": count(m.Ticket),
        "orders": count(m.OrderRecord),
        "cases": count(m.Case),
        "categories": count(m.Category),
        "procedures": count(m.Procedure),
        "policy_documents": count(m.PolicyDocument),
        "clauses": count(m.Clause),
        "rule_pack": {"version": pack.version, "rules": pack.rule_count, "conflicts": len(pack.conflicts or [])}
        if pack
        else None,
        "router": {"version": router_row.version, "metrics": router_row.metrics} if router_row else None,
        "drift": {k: v for k, v in drift},
        "splits": {str(k): v for k, v in gold},
        "eval_scenarios": count(m.EvalScenario),
    }


@router.get("/categories")
def categories(workspace_id: str, session: Session = Depends(db_session)) -> list[dict]:
    _ws(session, workspace_id)
    rows = session.execute(
        select(m.Category).where(m.Category.workspace_id == workspace_id).order_by(m.Category.pain_score.desc())
    ).scalars()
    return [
        {
            "key": c.key,
            "name": c.name,
            "description": c.description,
            "volume": c.volume,
            "avg_hours_to_resolve": float(c.avg_hours_to_resolve) if c.avg_hours_to_resolve is not None else None,
            "pain_score": float(c.pain_score or 0),
            "status": c.status,
            "approved_by": c.approved_by,
        }
        for c in rows
    ]


@router.get("/procedures")
def procedures(workspace_id: str, session: Session = Depends(db_session)) -> list[dict]:
    _ws(session, workspace_id)
    rows = session.execute(
        select(m.Procedure).where(m.Procedure.workspace_id == workspace_id).order_by(m.Procedure.category_key)
    ).scalars()
    return [
        {
            "category_key": p.category_key,
            "version": p.version,
            "name": p.name,
            "status": p.status,
            "approved_by": p.approved_by,
            "steps": p.steps,
            "required_facts": p.required_facts,
            "outcomes": p.outcomes,
            "mining": p.mining,
        }
        for p in rows
    ]


@router.get("/rules")
def rules(workspace_id: str, session: Session = Depends(db_session)) -> dict:
    _ws(session, workspace_id)
    pack = (
        session.execute(
            select(m.RulePack).where(m.RulePack.workspace_id == workspace_id, m.RulePack.status == "active")
        )
        .scalars()
        .first()
    )
    if pack is None:
        return {"version": None, "clauses": [], "rules": [], "conflicts": []}
    return {
        "version": pack.version,
        "clauses": pack.pack.get("clauses", []),
        "rules": pack.pack.get("rules", []),
        "conflicts": pack.conflicts,
    }


@router.get("/policy-documents")
def policy_documents(workspace_id: str, session: Session = Depends(db_session)) -> list[dict]:
    _ws(session, workspace_id)
    rows = session.execute(select(m.PolicyDocument).where(m.PolicyDocument.workspace_id == workspace_id)).scalars()
    return [
        {
            "id": str(d.id),
            "title": d.title,
            "source_url": d.source_url,
            "source_type": d.source_type,
            "tier": d.tier,
            "fetched_at": d.fetched_at.isoformat() if d.fetched_at else None,
            "chars": len(d.text or ""),
            "sha256": d.sha256,
            "status": d.status,
        }
        for d in rows
    ]


@router.get("/drift")
def drift(workspace_id: str, session: Session = Depends(db_session)) -> list[dict]:
    _ws(session, workspace_id)
    rows = session.execute(
        select(m.DriftFinding)
        .where(m.DriftFinding.workspace_id == workspace_id)
        .order_by(m.DriftFinding.kind, m.DriftFinding.support.desc())
    ).scalars()
    return [
        {
            "id": str(f.id),
            "kind": f.kind,
            "category_key": f.category_key,
            "rule_id": f.rule_id,
            "clause_id": f.clause_id,
            "staff_outcome": f.staff_outcome,
            "policy_outcome": f.policy_outcome,
            "pattern": f.pattern,
            "support": f.support,
            "status": f.status,
            "note": f.note,
        }
        for f in rows
    ]


@router.post("/drift/{finding_id}")
def set_drift_status(
    workspace_id: str, finding_id: str, status: str, note: str | None = None, session: Session = Depends(db_session)
) -> dict:
    _ws(session, workspace_id)
    allowed = {"open", "policy_is_right", "update_policy", "adopt_as_rule", "ignored"}
    if status not in allowed:
        raise HTTPException(400, f"status must be one of {sorted(allowed)}")
    f = session.get(m.DriftFinding, finding_id)
    if f is None or f.workspace_id != workspace_id:
        raise HTTPException(404, "finding not found")
    f.status = status
    f.note = note
    session.add(f)
    session.commit()
    adopted = None
    if status == "adopt_as_rule" and f.kind == "unwritten_rule":
        from ...onboard.pipeline import adopt_unwritten_rule

        adopted = adopt_unwritten_rule(session, workspace_id, f)
    return {"id": finding_id, "status": status, "adopted_rule_id": adopted}


@router.get("/cases")
def cases(
    workspace_id: str,
    limit: int = 50,
    category: str | None = None,
    drift_only: bool = False,
    session: Session = Depends(db_session),
) -> list[dict]:
    _ws(session, workspace_id)
    q = select(m.Case).where(m.Case.workspace_id == workspace_id)
    if category:
        q = q.where(m.Case.category_key == category)
    if drift_only:
        q = q.where(m.Case.is_drift.is_(True))
    rows = session.execute(q.order_by(m.Case.ticket_id).limit(limit)).scalars()
    return [
        {
            "ticket_id": c.ticket_id,
            "category_key": c.category_key,
            "summary": c.summary,
            "facts": c.facts,
            "record": c.record,
            "hours_to_resolve": float(c.hours_to_resolve) if c.hours_to_resolve is not None else None,
            "is_drift": c.is_drift,
            "drift_reason": c.drift_reason,
            "is_gold": c.is_gold,
            "split": c.split,
        }
        for c in rows
    ]
