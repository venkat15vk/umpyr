from __future__ import annotations

from sqlalchemy.orm import Session

from ..db.models import Workspace


def ensure_workspace(session: Session, workspace_id: str, name: str, kind: str = "sandbox") -> Workspace:
    ws = session.get(Workspace, workspace_id)
    if ws is None:
        ws = Workspace(
            id=workspace_id,
            name=name,
            kind=kind,
            status="onboarding",
            settings={"mode": "autonomous", "auto_approve_limit": 100, "reply_channel": "email"},
            is_synthetic=True,
        )
        session.add(ws)
        session.commit()
    return ws
