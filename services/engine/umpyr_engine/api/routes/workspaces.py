from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import Workspace
from ..deps import db_session

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def _ws(w: Workspace) -> dict:
    return {
        "id": w.id,
        "name": w.name,
        "kind": w.kind,
        "status": w.status,
        "settings": w.settings,
        "is_synthetic": w.is_synthetic,
        "expires_at": w.expires_at.isoformat() if w.expires_at else None,
        "created_at": w.created_at.isoformat() if w.created_at else None,
    }


@router.get("")
def list_workspaces(session: Session = Depends(db_session)) -> list[dict]:
    return [_ws(w) for w in session.execute(select(Workspace).order_by(Workspace.created_at)).scalars()]


@router.get("/{workspace_id}")
def get_workspace(workspace_id: str, session: Session = Depends(db_session)) -> dict:
    w = session.get(Workspace, workspace_id)
    if w is None:
        raise HTTPException(404, "workspace not found")
    return _ws(w)
